#include "thread_parent_info.h"

#include <cstdio>

namespace esphome {
namespace thread_parent_info {

static const char *const TAG = "thread_parent_info";

void ThreadParentInfoComponent::setup() {
  if (this->event_based_) {
    // PollingComponent starts its poller before setup(). Stop it when the
    // component is configured for OpenThread state-change driven updates.
    this->stop_poller();
    this->request_refresh_();
#ifdef USE_OPENTHREAD
    this->register_state_changed_callback_();
#endif
  }
}

void ThreadParentInfoComponent::loop() {
  if (!this->event_based_) {
    return;
  }

#ifdef USE_OPENTHREAD
  if (!this->state_callback_registered_) {
    const uint32_t now = millis();
    if (this->last_registration_attempt_ms_ == 0 || now - this->last_registration_attempt_ms_ >= 5000) {
      this->register_state_changed_callback_();
    }
  }
#endif

  if (this->refresh_requested_) {
    this->refresh_requested_ = false;
    this->publish_parent_info_();
  }
}

bool ThreadParentInfoComponent::teardown() {
#ifdef USE_OPENTHREAD
  this->remove_state_changed_callback_();
#endif
  return true;
}

void ThreadParentInfoComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Thread Parent Info:");
  ESP_LOGCONFIG(TAG, "  Update Mode: %s", this->event_based_ ? "event-based" : "polling");
  if (!this->event_based_) {
    LOG_UPDATE_INTERVAL(this);
  } else {
    ESP_LOGCONFIG(TAG, "  Update Interval: disabled in event-based mode");
#ifdef USE_OPENTHREAD
    ESP_LOGCONFIG(TAG, "  OpenThread state callback: %s", this->state_callback_registered_ ? "registered" : "not registered yet");
#endif
  }
  LOG_TEXT_SENSOR("  ", "Parent Extended Address", this->parent_extaddr_sensor_);
  LOG_TEXT_SENSOR("  ", "Parent RLOC16", this->parent_rloc16_sensor_);
}

void ThreadParentInfoComponent::request_refresh_() {
  this->refresh_requested_ = true;
}

void ThreadParentInfoComponent::publish_parent_extaddr_(const std::string &value) {
  if (this->parent_extaddr_sensor_ != nullptr && value != this->last_parent_extaddr_) {
    this->parent_extaddr_sensor_->publish_state(value);
  }
  this->last_parent_extaddr_ = value;
}

void ThreadParentInfoComponent::publish_parent_rloc16_(const std::string &value) {
  if (this->parent_rloc16_sensor_ != nullptr && value != this->last_parent_rloc16_) {
    this->parent_rloc16_sensor_->publish_state(value);
  }
  this->last_parent_rloc16_ = value;
}

void ThreadParentInfoComponent::publish_both_(const std::string &value) {
  this->publish_parent_extaddr_(value);
  this->publish_parent_rloc16_(value);
}

void ThreadParentInfoComponent::update() {
  this->publish_parent_info_();
}

void ThreadParentInfoComponent::publish_parent_info_() {
#ifndef USE_OPENTHREAD
  this->publish_both_("OpenThread not enabled");
  return;
#else
  auto lock = esphome::openthread::InstanceLock::try_acquire(10);
  if (!lock) {
    ESP_LOGV(TAG, "OpenThread instance lock unavailable");
    return;
  }

  otInstance *instance = lock->get_instance();
  if (instance == nullptr) {
    this->publish_both_("OpenThread instance unavailable");
    return;
  }

  if (otThreadGetDeviceRole(instance) != OT_DEVICE_ROLE_CHILD) {
    this->publish_both_("no parent");
    return;
  }

  otRouterInfo parent{};
  if (otThreadGetParentInfo(instance, &parent) != OT_ERROR_NONE) {
    this->publish_both_("parent unavailable");
    return;
  }

  this->publish_parent_extaddr_(extaddr_to_string_(parent.mExtAddress));
  this->publish_parent_rloc16_(rloc16_to_string_(parent.mRloc16));
#endif
}

#ifdef USE_OPENTHREAD
bool ThreadParentInfoComponent::register_state_changed_callback_() {
  this->last_registration_attempt_ms_ = millis();

  auto lock = esphome::openthread::InstanceLock::try_acquire(10);
  if (!lock) {
    ESP_LOGV(TAG, "OpenThread instance lock unavailable while registering state callback");
    return false;
  }

  otInstance *instance = lock->get_instance();
  if (instance == nullptr) {
    ESP_LOGV(TAG, "OpenThread instance unavailable while registering state callback");
    return false;
  }

  otError error = otSetStateChangedCallback(instance, &ThreadParentInfoComponent::ot_state_changed_callback_, this);
  if (error == OT_ERROR_NONE || error == OT_ERROR_ALREADY) {
    this->state_callback_registered_ = true;
    ESP_LOGD(TAG, "OpenThread state callback registered");
    return true;
  }

  ESP_LOGW(TAG, "Failed to register OpenThread state callback: %d", static_cast<int>(error));
  return false;
}

void ThreadParentInfoComponent::remove_state_changed_callback_() {
  if (!this->state_callback_registered_) {
    return;
  }

  auto lock = esphome::openthread::InstanceLock::try_acquire(10);
  if (lock) {
    otInstance *instance = lock->get_instance();
    if (instance != nullptr) {
      otRemoveStateChangeCallback(instance, &ThreadParentInfoComponent::ot_state_changed_callback_, this);
    }
  }

  this->state_callback_registered_ = false;
}

void ThreadParentInfoComponent::ot_state_changed_callback_(otChangedFlags flags, void *context) {
  if (context == nullptr) {
    return;
  }
  static_cast<ThreadParentInfoComponent *>(context)->handle_ot_state_changed_(flags);
}

void ThreadParentInfoComponent::handle_ot_state_changed_(otChangedFlags flags) {
  if (!this->event_based_) {
    return;
  }
  if (!parent_relevant_flags_(flags)) {
    return;
  }

  this->request_refresh_();
}

bool ThreadParentInfoComponent::parent_relevant_flags_(otChangedFlags flags) {
  constexpr otChangedFlags relevant_flags = OT_CHANGED_THREAD_ROLE | OT_CHANGED_THREAD_LL_ADDR | OT_CHANGED_THREAD_ML_ADDR |
                                           OT_CHANGED_THREAD_RLOC_ADDED | OT_CHANGED_THREAD_RLOC_REMOVED |
                                           OT_CHANGED_THREAD_PARTITION_ID;
  return (flags & relevant_flags) != 0;
}

std::string ThreadParentInfoComponent::extaddr_to_string_(const otExtAddress &address) {
  const uint8_t *e = address.m8;
  char buf[17];
  std::snprintf(
      buf, sizeof(buf), "%02x%02x%02x%02x%02x%02x%02x%02x", e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7]);
  return std::string(buf);
}

std::string ThreadParentInfoComponent::rloc16_to_string_(uint16_t rloc16) {
  char buf[5];
  std::snprintf(buf, sizeof(buf), "%04x", rloc16);
  return std::string(buf);
}
#endif

}  // namespace thread_parent_info
}  // namespace esphome
