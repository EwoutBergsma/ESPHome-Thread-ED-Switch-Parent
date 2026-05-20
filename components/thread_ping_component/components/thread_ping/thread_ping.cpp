#include "thread_ping.h"

#include <cstdio>
#include <cstring>

namespace esphome {
namespace thread_ping {

static const char *const TAG = "thread_ping";
static constexpr uint32_t AUTO_START_DELAY_MS = 5000;
static constexpr uint16_t SINGLE_PING_COUNT = 1;
static constexpr uint32_t SINGLE_PING_INTERVAL_MS = 0;

void ThreadPingComponent::setup() {
  this->publish_state_("stopped");
  this->publish_result_("idle");

  if (this->auto_start_) {
    this->set_timeout("thread_ping_auto_start", AUTO_START_DELAY_MS, [this]() { this->start(); });
  }
}

void ThreadPingComponent::loop() {
  if (this->statistics_ready_.exchange(false)) {
    this->process_statistics_();
  }

  if (!this->run_enabled_ || this->ping_in_flight_) {
    return;
  }

  const uint32_t now = millis();
  if (this->next_ping_due_ms_ == 0 || static_cast<int32_t>(now - this->next_ping_due_ms_) >= 0) {
    this->begin_parent_ping_();
  }
}

void ThreadPingComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Thread Ping:");
  ESP_LOGCONFIG(TAG, "  Target: current Thread parent RLOC");
  ESP_LOGCONFIG(TAG, "  Mode: one ICMPv6 Echo Request per interval");
  ESP_LOGCONFIG(TAG, "  Auto Start: %s", YESNO(this->auto_start_));
  ESP_LOGCONFIG(TAG, "  Auto Interval: %u ms", this->auto_interval_ms_);
  ESP_LOGCONFIG(TAG, "  Timeout: %u ms", this->timeout_ms_);
  LOG_TEXT_SENSOR("  ", "State", this->state_sensor_);
  LOG_TEXT_SENSOR("  ", "Last Result", this->last_result_sensor_);
  LOG_TEXT_SENSOR("  ", "Target ExtAddr", this->target_extaddr_sensor_);
  LOG_TEXT_SENSOR("  ", "Target RLOC16", this->target_rloc16_sensor_);
  LOG_TEXT_SENSOR("  ", "Target Address", this->target_address_sensor_);
  LOG_SENSOR("  ", "Sent", this->sent_sensor_);
  LOG_SENSOR("  ", "Received", this->received_sensor_);
  LOG_SENSOR("  ", "Loss", this->loss_sensor_);
  LOG_SENSOR("  ", "RTT", this->rtt_sensor_);
}

void ThreadPingComponent::start() {
  if (this->run_enabled_) {
    ESP_LOGD(TAG, "Automatic parent pinging is already running");
    return;
  }

  ESP_LOGI(TAG, "Starting automatic current-parent pinging");
  this->run_enabled_ = true;
  this->schedule_next_ping_(0);
  this->publish_state_("waiting");
  this->publish_result_("started");
}

void ThreadPingComponent::stop() {
  if (!this->run_enabled_ && !this->ping_in_flight_) {
    ESP_LOGD(TAG, "Automatic parent pinging is already stopped");
    this->publish_state_("stopped");
    this->publish_result_("stopped");
    return;
  }

  ESP_LOGI(TAG, "Stopping automatic current-parent pinging");
  this->run_enabled_ = false;
  this->next_ping_due_ms_ = 0;

#ifdef USE_OPENTHREAD
  if (this->ping_in_flight_) {
    auto lock = esphome::openthread::InstanceLock::try_acquire(10);
    if (lock) {
      otInstance *instance = lock->get_instance();
      if (instance != nullptr) {
        otPingSenderStop(instance);
      }
    }
  }
#endif

  this->ping_in_flight_ = false;
  this->expecting_statistics_ = false;
  this->statistics_ready_.store(false);
  this->publish_state_("stopped");
  this->publish_result_("stopped");
}

void ThreadPingComponent::toggle() {
  if (this->run_enabled_ || this->ping_in_flight_) {
    this->stop();
  } else {
    this->start();
  }
}

void ThreadPingComponent::ping_parent_once() {
  if (this->ping_in_flight_) {
    ESP_LOGW(TAG, "Cannot start one-shot parent ping: ping already active");
    this->publish_result_("busy");
    return;
  }
  this->begin_parent_ping_();
}

void ThreadPingComponent::schedule_next_ping_(uint32_t delay_ms) {
  if (delay_ms == 0) {
    this->next_ping_due_ms_ = 0;
  } else {
    this->next_ping_due_ms_ = millis() + delay_ms;
  }
}

void ThreadPingComponent::begin_parent_ping_() {
#ifndef USE_OPENTHREAD
  this->publish_result_("OpenThread not enabled");
  this->publish_state_(this->run_enabled_ ? "waiting" : "stopped");
  if (this->run_enabled_) {
    this->schedule_next_ping_(this->auto_interval_ms_);
  }
  return;
#else
  if (this->ping_in_flight_) {
    this->publish_result_("busy");
    return;
  }

  auto lock = esphome::openthread::InstanceLock::try_acquire(10);
  if (!lock) {
    ESP_LOGV(TAG, "OpenThread instance lock unavailable");
    this->publish_result_("OpenThread lock unavailable");
    if (this->run_enabled_) {
      this->publish_state_("waiting");
      this->schedule_next_ping_(this->auto_interval_ms_);
    }
    return;
  }

  otInstance *instance = lock->get_instance();
  if (instance == nullptr) {
    this->publish_result_("OpenThread instance unavailable");
    if (this->run_enabled_) {
      this->publish_state_("waiting");
      this->schedule_next_ping_(this->auto_interval_ms_);
    }
    return;
  }

  ParentSnapshot parent{};
  if (!this->read_current_parent_(instance, &parent)) {
    ESP_LOGD(TAG, "No current Thread parent to ping");
    this->clear_target_();
    this->publish_counts_(0, 0, 0);
    this->publish_loss_(0, 0);
    this->publish_result_("no parent");
    if (this->run_enabled_) {
      this->publish_state_("waiting");
      this->schedule_next_ping_(this->auto_interval_ms_);
    }
    return;
  }

  otPingSenderConfig config{};
  config.mDestination = parent.address;
  config.mCount = SINGLE_PING_COUNT;
  config.mInterval = SINGLE_PING_INTERVAL_MS;
  config.mTimeout = this->timeout_ms_;
  config.mReplyCallback = &ThreadPingComponent::reply_callback_;
  config.mStatisticsCallback = &ThreadPingComponent::statistics_callback_;
  config.mCallbackContext = this;

  otError error = otPingSenderPing(instance, &config);
  if (error != OT_ERROR_NONE) {
    ESP_LOGW(TAG, "Failed to start parent ping to %s: OpenThread error %d", parent.address_string.c_str(), static_cast<int>(error));
    this->publish_target_(parent);
    this->publish_counts_(0, 0, 0);
    this->publish_loss_(0, 0);
    this->publish_result_(error == OT_ERROR_BUSY ? "busy" : "failed to start");
    if (this->run_enabled_) {
      this->publish_state_("waiting");
      this->schedule_next_ping_(this->auto_interval_ms_);
    }
    return;
  }

  this->target_parent_ = parent;
  this->ping_in_flight_ = true;
  this->expecting_statistics_ = true;
  this->statistics_ready_.store(false);
  this->cb_sent_count_ = 0;
  this->cb_received_count_ = 0;
  this->cb_last_rtt_ms_ = 0;

  this->publish_target_(parent);
  this->publish_state_("pinging");
  this->publish_result_("pinging");
  ESP_LOGI(TAG, "Pinging current Thread parent ExtAddr %s RLOC16 %s at %s (single packet)", parent.extaddr.c_str(),
           parent.rloc16_string.c_str(), parent.address_string.c_str());
#endif
}

void ThreadPingComponent::process_statistics_() {
  if (!this->expecting_statistics_) {
    return;
  }

  this->expecting_statistics_ = false;
  this->ping_in_flight_ = false;

  const uint16_t sent = this->cb_sent_count_;
  const uint16_t received = this->cb_received_count_;
  const uint16_t rtt = received > 0 ? this->cb_last_rtt_ms_ : 0;

  this->publish_counts_(sent, received, rtt);
  this->publish_loss_(sent, received);

  std::string result;

#ifdef USE_OPENTHREAD
  ParentSnapshot current{};
  bool parent_changed = true;
  auto lock = esphome::openthread::InstanceLock::try_acquire(10);
  if (lock) {
    otInstance *instance = lock->get_instance();
    if (instance != nullptr && this->read_current_parent_(instance, &current)) {
      parent_changed = !this->parent_matches_(this->target_parent_, current);
    }
  }

  if (parent_changed) {
    result = "parent changed during ping";
  } else
#endif
  if (sent == 0) {
    result = "no packets sent";
  } else if (received == 1) {
    result = "success";
  } else {
    result = "timeout";
  }

  ESP_LOGI(TAG, "Parent ping result: %s; sent=%u received=%u loss=%.0f%% rtt=%u ms target=%s", result.c_str(), sent,
           received, sent > 0 ? (100.0f * static_cast<float>(sent - received) / static_cast<float>(sent)) : 0.0f, rtt,
           this->target_parent_.address_string.c_str());

  this->publish_result_(result);

  if (this->run_enabled_) {
    this->publish_state_("waiting");
    this->schedule_next_ping_(this->auto_interval_ms_);
  } else {
    this->publish_state_("stopped");
  }
}

void ThreadPingComponent::publish_state_(const std::string &value) {
  if (this->state_sensor_ != nullptr) {
    this->state_sensor_->publish_state(value);
  }
}

void ThreadPingComponent::publish_result_(const std::string &value) {
  if (this->last_result_sensor_ != nullptr) {
    this->last_result_sensor_->publish_state(value);
  }
}

void ThreadPingComponent::publish_target_(const ParentSnapshot &parent) {
  if (this->target_extaddr_sensor_ != nullptr) {
    this->target_extaddr_sensor_->publish_state(parent.extaddr);
  }
  if (this->target_rloc16_sensor_ != nullptr) {
    this->target_rloc16_sensor_->publish_state(parent.rloc16_string);
  }
  if (this->target_address_sensor_ != nullptr) {
    this->target_address_sensor_->publish_state(parent.address_string);
  }
}

void ThreadPingComponent::publish_counts_(uint16_t sent, uint16_t received, uint16_t rtt_ms) {
  if (this->sent_sensor_ != nullptr) {
    this->sent_sensor_->publish_state(sent);
  }
  if (this->received_sensor_ != nullptr) {
    this->received_sensor_->publish_state(received);
  }
  if (this->rtt_sensor_ != nullptr) {
    this->rtt_sensor_->publish_state(rtt_ms);
  }
}

void ThreadPingComponent::publish_loss_(uint16_t sent, uint16_t received) {
  if (this->loss_sensor_ == nullptr) {
    return;
  }

  if (sent == 0) {
    this->loss_sensor_->publish_state(0);
    return;
  }

  const float loss = 100.0f * static_cast<float>(sent - received) / static_cast<float>(sent);
  this->loss_sensor_->publish_state(loss);
}

void ThreadPingComponent::clear_target_() {
  ParentSnapshot empty{};
  empty.extaddr = "no parent";
  empty.rloc16_string = "no parent";
  empty.address_string = "no parent";
  this->publish_target_(empty);
}

#ifdef USE_OPENTHREAD
bool ThreadPingComponent::read_current_parent_(otInstance *instance, ParentSnapshot *parent) {
  if (instance == nullptr || parent == nullptr) {
    return false;
  }

  if (otThreadGetDeviceRole(instance) != OT_DEVICE_ROLE_CHILD) {
    return false;
  }

  otRouterInfo parent_info{};
  if (otThreadGetParentInfo(instance, &parent_info) != OT_ERROR_NONE) {
    return false;
  }

  parent->valid = true;
  parent->rloc16 = parent_info.mRloc16;
  parent->extaddr = extaddr_to_string_(parent_info.mExtAddress);
  parent->rloc16_string = rloc16_to_string_(parent_info.mRloc16);
  make_rloc_address_(instance, parent_info.mRloc16, &parent->address);
  parent->address_string = ip6_to_string_(parent->address);
  return true;
}

bool ThreadPingComponent::parent_matches_(const ParentSnapshot &a, const ParentSnapshot &b) const {
  return a.valid && b.valid && a.rloc16 == b.rloc16 && a.extaddr == b.extaddr;
}

void ThreadPingComponent::reply_callback_(const otPingSenderReply *reply, void *context) {
  if (context == nullptr || reply == nullptr) {
    return;
  }
  static_cast<ThreadPingComponent *>(context)->handle_reply_(reply);
}

void ThreadPingComponent::statistics_callback_(const otPingSenderStatistics *statistics, void *context) {
  if (context == nullptr || statistics == nullptr) {
    return;
  }
  static_cast<ThreadPingComponent *>(context)->handle_statistics_(statistics);
}

void ThreadPingComponent::handle_reply_(const otPingSenderReply *reply) {
  this->cb_last_rtt_ms_ = reply->mRoundTripTime;
  ESP_LOGV(TAG, "Parent ping reply: seq=%u rtt=%u ms size=%u", reply->mSequenceNumber, reply->mRoundTripTime, reply->mSize);
}

void ThreadPingComponent::handle_statistics_(const otPingSenderStatistics *statistics) {
  if (!this->expecting_statistics_) {
    return;
  }

  this->cb_sent_count_ = statistics->mSentCount;
  this->cb_received_count_ = statistics->mReceivedCount;
  if (statistics->mReceivedCount > 0 && this->cb_last_rtt_ms_ == 0) {
    // For a single-packet run, the average is the packet RTT. Keep this fallback
    // in case a platform delivers statistics without an earlier reply callback.
    this->cb_last_rtt_ms_ = static_cast<uint16_t>(statistics->mTotalRoundTripTime / statistics->mReceivedCount);
  }
  this->statistics_ready_.store(true);
}

void ThreadPingComponent::make_rloc_address_(otInstance *instance, uint16_t rloc16, otIp6Address *address) {
  std::memset(address, 0, sizeof(otIp6Address));

  const otMeshLocalPrefix *prefix = otThreadGetMeshLocalPrefix(instance);
  if (prefix != nullptr) {
    std::memcpy(&address->mFields.m8[0], &prefix->m8[0], 8);
  }

  // Thread RLOC IID format: 0000:00ff:fe00:<RLOC16>.
  address->mFields.m8[8] = 0x00;
  address->mFields.m8[9] = 0x00;
  address->mFields.m8[10] = 0x00;
  address->mFields.m8[11] = 0xff;
  address->mFields.m8[12] = 0xfe;
  address->mFields.m8[13] = 0x00;
  address->mFields.m8[14] = static_cast<uint8_t>(rloc16 >> 8);
  address->mFields.m8[15] = static_cast<uint8_t>(rloc16 & 0xff);
}

std::string ThreadPingComponent::ip6_to_string_(const otIp6Address &address) {
  char buf[OT_IP6_ADDRESS_STRING_SIZE];
  otIp6AddressToString(&address, buf, sizeof(buf));
  return std::string(buf);
}

std::string ThreadPingComponent::extaddr_to_string_(const otExtAddress &address) {
  const uint8_t *e = address.m8;
  char buf[17];
  std::snprintf(buf, sizeof(buf), "%02x%02x%02x%02x%02x%02x%02x%02x", e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7]);
  return std::string(buf);
}

std::string ThreadPingComponent::rloc16_to_string_(uint16_t rloc16) {
  char buf[5];
  std::snprintf(buf, sizeof(buf), "%04x", rloc16);
  return std::string(buf);
}
#endif

}  // namespace thread_ping
}  // namespace esphome
