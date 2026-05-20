#include "thread_parent_info.h"

#include <cstdio>

namespace esphome {
namespace thread_parent_info {

static const char *const TAG = "thread_parent_info";

void ThreadParentInfoComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Thread Parent Info:");
  LOG_UPDATE_INTERVAL(this);
  LOG_TEXT_SENSOR("  ", "Parent Extended Address", this->parent_extaddr_sensor_);
  LOG_TEXT_SENSOR("  ", "Parent RLOC16", this->parent_rloc16_sensor_);
}

void ThreadParentInfoComponent::publish_parent_extaddr_(const std::string &value) {
  if (this->parent_extaddr_sensor_ != nullptr) {
    this->parent_extaddr_sensor_->publish_state(value);
  }
}

void ThreadParentInfoComponent::publish_parent_rloc16_(const std::string &value) {
  if (this->parent_rloc16_sensor_ != nullptr) {
    this->parent_rloc16_sensor_->publish_state(value);
  }
}

void ThreadParentInfoComponent::publish_both_(const std::string &value) {
  this->publish_parent_extaddr_(value);
  this->publish_parent_rloc16_(value);
}

void ThreadParentInfoComponent::update() {
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
