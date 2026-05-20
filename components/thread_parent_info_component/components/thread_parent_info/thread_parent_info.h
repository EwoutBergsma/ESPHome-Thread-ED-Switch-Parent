#pragma once

#include "esphome/core/component.h"
#include "esphome/core/log.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/openthread/openthread.h"

#include <string>

#ifdef USE_OPENTHREAD
#include <openthread/thread.h>
#endif

namespace esphome {
namespace thread_parent_info {

class ThreadParentInfoComponent : public PollingComponent {
 public:
  void set_parent_extaddr_sensor(text_sensor::TextSensor *sensor) { this->parent_extaddr_sensor_ = sensor; }
  void set_parent_rloc16_sensor(text_sensor::TextSensor *sensor) { this->parent_rloc16_sensor_ = sensor; }

  void update() override;
  void dump_config() override;

 protected:
  text_sensor::TextSensor *parent_extaddr_sensor_{nullptr};
  text_sensor::TextSensor *parent_rloc16_sensor_{nullptr};

  void publish_parent_extaddr_(const std::string &value);
  void publish_parent_rloc16_(const std::string &value);
  void publish_both_(const std::string &value);

#ifdef USE_OPENTHREAD
  static std::string extaddr_to_string_(const otExtAddress &address);
  static std::string rloc16_to_string_(uint16_t rloc16);
#endif
};

}  // namespace thread_parent_info
}  // namespace esphome
