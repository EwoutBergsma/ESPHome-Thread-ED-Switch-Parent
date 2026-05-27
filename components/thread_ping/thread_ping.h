#pragma once

#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "esphome/components/button/button.h"
#include "esphome/components/switch/switch.h"
#include "esphome/components/openthread/openthread.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"

#include <atomic>
#include <cstdint>
#include <string>

#ifdef USE_OPENTHREAD
#include <openthread/dataset.h>
#include <openthread/instance.h>
#include <openthread/ip6.h>
#include <openthread/ping_sender.h>
#include <openthread/platform/radio.h>
#include <openthread/thread.h>
#endif

namespace esphome {
namespace thread_ping {

class ThreadPingControlSwitch;

class ThreadPingComponent : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI - 30.0f; }

  void set_auto_start(bool auto_start) { this->auto_start_ = auto_start; }
  void set_auto_interval_ms(uint32_t auto_interval_ms) { this->auto_interval_ms_ = auto_interval_ms; }
  void set_timeout_ms(uint16_t timeout_ms) { this->timeout_ms_ = timeout_ms; }

  void set_state_sensor(text_sensor::TextSensor *sensor) { this->state_sensor_ = sensor; }
  void set_last_result_sensor(text_sensor::TextSensor *sensor) { this->last_result_sensor_ = sensor; }
  void set_target_extaddr_sensor(text_sensor::TextSensor *sensor) { this->target_extaddr_sensor_ = sensor; }
  void set_target_rloc16_sensor(text_sensor::TextSensor *sensor) { this->target_rloc16_sensor_ = sensor; }
  void set_target_address_sensor(text_sensor::TextSensor *sensor) { this->target_address_sensor_ = sensor; }

  void set_sent_sensor(sensor::Sensor *sensor) { this->sent_sensor_ = sensor; }
  void set_received_sensor(sensor::Sensor *sensor) { this->received_sensor_ = sensor; }
  void set_loss_sensor(sensor::Sensor *sensor) { this->loss_sensor_ = sensor; }
  void set_rtt_sensor(sensor::Sensor *sensor) { this->rtt_sensor_ = sensor; }
  void set_rss_sensor(sensor::Sensor *sensor) { this->rss_sensor_ = sensor; }
  void set_control_switch(ThreadPingControlSwitch *control_switch) { this->control_switch_ = control_switch; }

  void start();
  void stop();
  void toggle();
  void ping_parent_once();
  bool is_running() const { return this->run_enabled_; }
  bool is_ping_in_flight() const { return this->ping_in_flight_; }

 protected:
  struct ParentSnapshot {
    bool valid{false};
    uint16_t rloc16{0};
    std::string extaddr;
    std::string rloc16_string;
    std::string address_string;
#ifdef USE_OPENTHREAD
    otIp6Address address{};
#endif
  };

  text_sensor::TextSensor *state_sensor_{nullptr};
  text_sensor::TextSensor *last_result_sensor_{nullptr};
  text_sensor::TextSensor *target_extaddr_sensor_{nullptr};
  text_sensor::TextSensor *target_rloc16_sensor_{nullptr};
  text_sensor::TextSensor *target_address_sensor_{nullptr};

  sensor::Sensor *sent_sensor_{nullptr};
  sensor::Sensor *received_sensor_{nullptr};
  sensor::Sensor *loss_sensor_{nullptr};
  sensor::Sensor *rtt_sensor_{nullptr};
  sensor::Sensor *rss_sensor_{nullptr};

  ThreadPingControlSwitch *control_switch_{nullptr};

  bool auto_start_{false};
  bool run_enabled_{false};
  bool ping_in_flight_{false};
  bool expecting_statistics_{false};
  uint32_t next_ping_due_ms_{0};
  uint32_t current_ping_started_ms_{0};
  uint32_t ping_sequence_{0};

  uint32_t auto_interval_ms_{60000};
  uint16_t timeout_ms_{3000};

  ParentSnapshot target_parent_{};

  std::atomic<bool> statistics_ready_{false};
  uint16_t cb_sent_count_{0};
  uint16_t cb_received_count_{0};
  uint16_t cb_last_rtt_ms_{0};
  bool cb_last_rss_valid_{false};
  int8_t cb_last_rss_dbm_{0};

  void schedule_next_ping_(uint32_t delay_ms);
  void begin_parent_ping_();
  void process_statistics_();
  void publish_state_(const std::string &value);
  void publish_result_(const std::string &value);
  void publish_target_(const ParentSnapshot &parent);
  void publish_counts_(uint16_t sent, uint16_t received, uint16_t rtt_ms);
  void publish_loss_(uint16_t sent, uint16_t received);
  void publish_rss_(bool valid, int8_t rss_dbm);
  void clear_target_();
  void publish_control_switch_(bool state);
  static std::string rss_to_string_(bool valid, int8_t rss_dbm);

#ifdef USE_OPENTHREAD
  bool read_current_parent_(otInstance *instance, ParentSnapshot *parent);
  bool parent_matches_(const ParentSnapshot &a, const ParentSnapshot &b) const;
  static void reply_callback_(const otPingSenderReply *reply, void *context);
  static void statistics_callback_(const otPingSenderStatistics *statistics, void *context);
  void handle_reply_(const otPingSenderReply *reply);
  void handle_statistics_(const otPingSenderStatistics *statistics);
  static void make_rloc_address_(otInstance *instance, uint16_t rloc16, otIp6Address *address);
  static std::string ip6_to_string_(const otIp6Address &address);
  static std::string extaddr_to_string_(const otExtAddress &address);
  static std::string rloc16_to_string_(uint16_t rloc16);
#endif
};

class ThreadPingControlSwitch : public switch_::Switch, public Component {
 public:
  void set_parent(ThreadPingComponent *parent) { this->parent_ = parent; }

 protected:
  void write_state(bool state) override {
    if (this->parent_ == nullptr) {
      this->publish_state(false);
      return;
    }

    if (state) {
      this->parent_->start();
    } else {
      this->parent_->stop();
    }
  }

  ThreadPingComponent *parent_{nullptr};
};

class ThreadPingToggleButton : public button::Button, public Component {
 public:
  void set_parent(ThreadPingComponent *parent) { this->parent_ = parent; }

 protected:
  void press_action() override {
    if (this->parent_ != nullptr) {
      this->parent_->toggle();
    }
  }

  ThreadPingComponent *parent_{nullptr};
};

}  // namespace thread_ping
}  // namespace esphome
