#pragma once

#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/openthread/openthread.h"

#include <atomic>
#include <string>

#ifdef USE_OPENTHREAD
#include <openthread/instance.h>
#include <openthread/thread.h>
#endif

namespace esphome {
namespace thread_parent_info {

class ThreadParentInfoComponent : public PollingComponent {
 public:
  void set_parent_extaddr_sensor(text_sensor::TextSensor *sensor) { this->parent_extaddr_sensor_ = sensor; }
  void set_parent_rloc16_sensor(text_sensor::TextSensor *sensor) { this->parent_rloc16_sensor_ = sensor; }
  void set_event_based(bool event_based) { this->event_based_ = event_based; }

  void setup() override;
  void loop() override;
  void update() override;
  void dump_config() override;
  bool teardown() override;

  // Match ESPHome's own openthread_info sensors: run after OpenThread/network setup.
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI - 20.0f; }

 protected:
  text_sensor::TextSensor *parent_extaddr_sensor_{nullptr};
  text_sensor::TextSensor *parent_rloc16_sensor_{nullptr};

  bool event_based_{false};
  bool state_callback_registered_{false};
  bool callback_registration_enabled_{false};
  uint32_t last_registration_attempt_ms_{0};
  std::atomic<bool> refresh_requested_{false};

  std::string last_parent_extaddr_;
  std::string last_parent_rloc16_;

  void request_refresh_();
  void publish_parent_info_();
  void publish_parent_extaddr_(const std::string &value);
  void publish_parent_rloc16_(const std::string &value);
  void publish_both_(const std::string &value);

#ifdef USE_OPENTHREAD
  bool register_state_changed_callback_();
  void remove_state_changed_callback_();
  static void ot_state_changed_callback_(otChangedFlags flags, void *context);
  void handle_ot_state_changed_(otChangedFlags flags);
  static bool parent_relevant_flags_(otChangedFlags flags);
  static std::string extaddr_to_string_(const otExtAddress &address);
  static std::string rloc16_to_string_(uint16_t rloc16);
#endif
};

}  // namespace thread_parent_info
}  // namespace esphome
