#include "thread_preferred_parent.h"

namespace esphome {
namespace thread_preferred_parent {

static const char *const TAG = "thread_preferred_parent";

// Treat all-whitespace strings as an explicit "clear this setting" request.
/**
 * Check whether a string contains only whitespace.
 *
 * @param text String to inspect.
 * @return `true` when `text` has no non-whitespace characters.
 */
static bool is_blank_string_(const std::string &text) {
  return text.find_first_not_of(" \t\r\n") == std::string::npos;
}

/**
 * Initialize the preferred-parent component and register optional OT hooks.
 */
void ThreadPreferredParentComponent::setup() {
  ESP_LOGI(TAG, "Thread preferred-parent component initialized");

  // The OpenThread patch optionally exports a Parent Response callback bridge.
  // When present, it gives this component visibility into every Parent
  // Response seen during discovery so we can decide when to trigger the
  // selected-parent attach and later explain what happened in the logs.
  if (thread_preferred_parent_ot_register_parent_response_callback != nullptr) {
    thread_preferred_parent_ot_register_parent_response_callback(
        &ThreadPreferredParentComponent::parent_response_callback_, this);
    this->parent_response_callback_registered_ = true;
    ESP_LOGI(TAG, "OpenThread Parent Response reporting hook registered");
  } else {
    ESP_LOGW(TAG, "OpenThread Parent Response reporting hook is not available; patch script may not be applied yet");
  }
}

/**
 * Log the current component configuration and runtime capabilities.
 */
void ThreadPreferredParentComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Thread preferred parent:");
  ESP_LOGCONFIG(TAG, "  Target type: %s", target_type_to_string_(this->target_type_));
  ESP_LOGCONFIG(TAG, "  Target: %s", this->target_to_string_().c_str());
  ESP_LOGCONFIG(TAG, "  Max attempts: %u", this->max_attempts_);
  ESP_LOGCONFIG(TAG, "  Retry interval: %u ms", this->retry_interval_ms_);
  ESP_LOGCONFIG(TAG, "  Selected attach timeout: %u ms", this->selected_attach_timeout_ms_);
  ESP_LOGCONFIG(TAG, "  Parent Request unicast: %s", YESNO(this->parent_request_unicast_));
  ESP_LOGCONFIG(TAG, "  Early attach on target: %s", YESNO(this->early_attach_on_target_));
  ESP_LOGCONFIG(TAG, "  Early attach delay: %u ms", this->early_attach_delay_ms_);
  ESP_LOGCONFIG(TAG, "  Require selected-parent hook: %s", YESNO(this->require_selected_parent_hook_));
  ESP_LOGCONFIG(TAG, "  Discovery hook available: %s", YESNO(this->discovery_hook_available_()));
  ESP_LOGCONFIG(TAG, "  Discovery unicast hook available: %s", YESNO(this->discovery_unicast_hook_available_()));
  ESP_LOGCONFIG(TAG, "  Selected-parent hook available: %s", YESNO(this->selected_parent_hook_available_()));
  ESP_LOGCONFIG(TAG, "  Parent Response reporting hook registered: %s", YESNO(this->parent_response_callback_registered_));
  ESP_LOGCONFIG(TAG, "  Log Parent Responses: %s", YESNO(this->log_parent_responses_));
  ESP_LOGCONFIG(TAG, "  Status: %s", status_to_string_(this->status_));
}

/**
 * Set the preferred parent target by RLOC16.
 *
 * @param rloc16 Preferred parent RLOC16.
 */
void ThreadPreferredParentComponent::set_parent_rloc16(uint16_t rloc16) {
  // Switching to an RLOC16 target invalidates any previously configured
  // extended-address target. The component always treats exactly one target
  // identifier as authoritative.
  this->target_type_ = TargetType::RLOC16;
  this->target_rloc16_ = rloc16;
  std::memset(&this->target_extaddr_, 0, sizeof(this->target_extaddr_));
  ESP_LOGI(TAG, "Configured preferred parent by RLOC16: 0x%04x", this->target_rloc16_);
}

/**
 * Parse and set the preferred parent target from an RLOC16 string.
 *
 * @param rloc16 Preferred parent RLOC16 in hexadecimal string form.
 * @return `true` when the target was accepted or cleared successfully.
 */
bool ThreadPreferredParentComponent::set_parent_rloc16(const std::string &rloc16) {
  if (is_blank_string_(rloc16)) {
    // A blank string is treated as an explicit clear request so service calls
    // and YAML templating can remove the current target without a separate API.
    if (this->target_type_ == TargetType::RLOC16) {
      this->clear_target();
    } else {
      this->target_rloc16_ = 0xFFFE;
    }
    ESP_LOGI(TAG, "Cleared preferred-parent RLOC16 target");
    return true;
  }

  uint16_t parsed = 0;
  if (!parse_rloc16_(rloc16, &parsed)) {
    ESP_LOGW(TAG, "Invalid preferred-parent RLOC16: %s", rloc16.c_str());
    this->set_status_(Status::INVALID_TARGET);
    return false;
  }

  this->set_parent_rloc16(parsed);
  return true;
}

/**
 * Parse and set the preferred parent target from an extended address string.
 *
 * @param extaddr Preferred parent extended address.
 * @return `true` when the target was accepted successfully.
 */
bool ThreadPreferredParentComponent::set_parent_extaddr(const std::string &extaddr) {
  otExtAddress parsed{};
  if (!this->parse_extaddr_(extaddr, &parsed)) {
    ESP_LOGW(TAG, "Invalid preferred-parent extended address: %s", extaddr.c_str());
    this->set_status_(Status::INVALID_TARGET);
    return false;
  }

  // As with the RLOC16 setter, selecting an ExtAddr target clears the other
  // identifier form so downstream matching logic has a single source of truth.
  this->target_type_ = TargetType::EXTADDR;
  this->target_rloc16_ = 0xFFFE;
  this->target_extaddr_ = parsed;
  ESP_LOGI(TAG, "Configured preferred parent by extended address: %s",
           this->extaddr_to_string_(this->target_extaddr_).c_str());
  return true;
}

/**
 * Start a preferred-parent switch using the currently configured target.
 */
void ThreadPreferredParentComponent::request_switch() {
  // Only one switch workflow may run at a time because discovery state,
  // buffered Parent Responses, and target-observation flags are all per-run.
  if (this->active_) {
    ESP_LOGW(TAG,
             "Ignoring preferred-parent switch request while busy: phase=%s status=%s active_target=%s attempt=%u/%u",
             phase_to_string_(this->phase_), status_to_string_(this->status_), this->target_to_string_().c_str(),
             this->attempts_, this->max_attempts_);
    return;
  }

  if (this->target_type_ == TargetType::NONE) {
    ESP_LOGW(TAG, "Refusing preferred-parent switch without a configured target identifier");
    this->set_status_(Status::INVALID_TARGET);
    return;
  }

  if (this->target_type_ == TargetType::RLOC16 && (this->target_rloc16_ == 0xFFFF || this->target_rloc16_ == 0xFFFE)) {
    ESP_LOGW(TAG, "Refusing preferred-parent switch with invalid RLOC16 0x%04x", this->target_rloc16_);
    this->set_status_(Status::INVALID_TARGET);
    return;
  }

  this->begin_switch_();
  ESP_LOGI(TAG, "Requested Thread parent switch to %s", this->target_to_string_().c_str());
}

/**
 * Set an RLOC16 target and immediately request a parent switch.
 *
 * @param rloc16 Preferred parent RLOC16.
 */
void ThreadPreferredParentComponent::request_switch(uint16_t rloc16) {
  this->set_parent_rloc16(rloc16);
  this->request_switch();
}

/**
 * Set an extended-address target and immediately request a parent switch.
 *
 * @param extaddr Preferred parent extended address.
 */
void ThreadPreferredParentComponent::request_switch(const std::string &extaddr) {
  if (!this->set_parent_extaddr(extaddr)) {
    return;
  }
  this->request_switch();
}

/**
 * Reset all per-attempt Parent Response tracking state.
 */
void ThreadPreferredParentComponent::reset_parent_response_tracking_() {
  // Reset per-attempt observation state before each discovery cycle.
  this->parent_response_count_ = 0;
  this->parent_response_last_dumped_count_ = 0;
  this->parent_response_target_count_ = 0;
  this->parent_response_buffer_head_ = 0;
  this->current_attempt_start_ms_ = millis();
  this->best_target_rssi_valid_ = false;
  this->best_target_rssi_ = -128;
  this->best_target_rloc16_ = 0xFFFE;
  this->discovery_target_observed_ms_ = 0;
  this->early_attach_pending_ = false;
  for (auto &entry : this->parent_response_buffer_) {
    entry = BufferedParentResponse{};
  }
}

/**
 * Reset state and enter the discovery phase for a new switch attempt.
 */
void ThreadPreferredParentComponent::begin_switch_() {
  // Start a fresh preferred-parent handoff attempt from the discovery phase.
  this->attempts_ = 0;
  this->active_ = true;
  this->phase_ = SwitchPhase::DISCOVERING;
  this->phase_deadline_ms_ = 0;
  this->attach_start_ms_ = 0;
  this->discovery_target_observed_ms_ = 0;
  this->early_attach_pending_ = false;
  this->target_observed_this_attempt_ = false;
  std::memset(&this->observed_target_extaddr_, 0, sizeof(this->observed_target_extaddr_));
  this->reset_parent_response_tracking_();
  this->set_status_(Status::DISCOVERING);
}

/**
 * Clear the configured target and return the component to its idle state.
 */
void ThreadPreferredParentComponent::clear_target() {
  // Reset all local targeting state first so any subsequent loop iteration sees
  // the component as fully idle even if OpenThread cleanup below cannot run.
  this->target_type_ = TargetType::NONE;
  this->target_rloc16_ = 0xFFFE;
  std::memset(&this->target_extaddr_, 0, sizeof(this->target_extaddr_));
  std::memset(&this->observed_target_extaddr_, 0, sizeof(this->observed_target_extaddr_));
  this->active_ = false;
  this->phase_ = SwitchPhase::IDLE;
  this->attempts_ = 0;
  this->set_status_(Status::IDLE);

  auto lock = esphome::openthread::InstanceLock::try_acquire(0);
  if (lock.has_value()) {
    // Best-effort cleanup: remove any preferred-parent hint that an older or
    // alternate patch may have left inside the OpenThread instance.
    this->clear_preferred_parent_in_ot_(lock->get_instance());
  }
}

/**
 * Advance the preferred-parent discovery and attach state machine.
 */
void ThreadPreferredParentComponent::loop() {
  const uint32_t now = millis();

  // Most iterations are intentionally cheap. The component becomes active only
  // while a requested parent switch is in progress.
  if (!this->active_) {
    return;
  }

  // OpenThread access is serialized through InstanceLock. If we cannot acquire
  // it right now, simply try again on the next loop without disturbing state.
  auto lock = esphome::openthread::InstanceLock::try_acquire(0);
  if (!lock.has_value()) {
    return;
  }

  otInstance *instance = lock->get_instance();

  // Success is defined by the device's actual current parent, not merely by
  // whether an attach request was accepted. This keeps the component grounded
  // in observed Thread state instead of optimistic API return values.
  if (this->current_parent_matches_(instance)) {
    const uint32_t attach_elapsed_ms = this->attach_start_ms_ == 0 ? 0 : now - this->attach_start_ms_;
    ESP_LOGI(TAG, "Thread parent switch succeeded; current parent is %s", this->target_to_string_().c_str());
    ESP_LOGI(TAG, "Attach result: success after %lu ms; %s selected",
             static_cast<unsigned long>(attach_elapsed_ms), this->target_to_string_().c_str());
    this->dump_buffered_parent_responses_("success target replay", ReplayMode::INFO_TARGET_ONLY);
    this->active_ = false;
    this->phase_ = SwitchPhase::IDLE;
    this->clear_preferred_parent_in_ot_(instance);
    this->set_status_(Status::SUCCESS);
    return;
  }

  switch (this->phase_) {
    case SwitchPhase::DISCOVERING: {
      // Wait until the active discovery window or early-attach debounce expires.
      if (this->phase_deadline_ms_ != 0 && static_cast<int32_t>(now - this->phase_deadline_ms_) < 0) {
        return;
      }

      if (this->phase_deadline_ms_ != 0) {
        // The current discovery window has ended; decide whether to attach or
        // schedule another discovery pass.
        const uint32_t discovery_elapsed_ms = now - this->current_attempt_start_ms_;
        const bool early_attach_deadline = this->early_attach_pending_ && this->target_observed_this_attempt_;
        this->log_discovery_summary_(early_attach_deadline ? "early target debounce complete" : "discovery window complete");

        if (this->target_observed_this_attempt_) {
          // We saw at least one Parent Response from the requested target during
          // this attempt, so discovery has done its job. The next step is to
          // convert that observation into a constrained selected-parent attach.
          if (early_attach_deadline) {
            ESP_LOGI(TAG,
                     "Discovery result: target observed after %lu ms; starting selected-parent attach after %lu ms total discovery time (%u ms early-attach delay)",
                     static_cast<unsigned long>(this->discovery_target_observed_ms_),
                     static_cast<unsigned long>(discovery_elapsed_ms), this->early_attach_delay_ms_);
          } else {
            ESP_LOGI(TAG,
                     "Discovery result: target observed after %lu ms; starting selected-parent attach after %lu ms discovery window",
                     static_cast<unsigned long>(this->discovery_target_observed_ms_),
                     static_cast<unsigned long>(discovery_elapsed_ms));
          }
          ESP_LOGI(TAG,
                   "Discovery-to-attach handoff: closing discovery attempt after %lu ms; buffered=%lu target_matches=%lu; in-flight Parent Responses may still be logged",
                   static_cast<unsigned long>(discovery_elapsed_ms),
                   static_cast<unsigned long>(this->parent_response_count_),
                   static_cast<unsigned long>(this->parent_response_target_count_));
          ESP_LOGI(TAG, "Preferred parent %s was observed; starting selected-parent attach", this->target_to_string_().c_str());
          otError attach_error = this->start_selected_parent_attach_(instance);
          if (attach_error == OT_ERROR_NONE) {
            // Discovery and attach use separate deadlines because the first is
            // a passive observation window, while the second waits for the real
            // Thread parent relationship to change.
            this->phase_ = SwitchPhase::ATTACHING;
            this->attach_start_ms_ = millis();
            this->phase_deadline_ms_ = now + this->selected_attach_timeout_ms_;
            this->early_attach_pending_ = false;
            this->set_status_(Status::ATTACHING);
            return;
          }

          ESP_LOGW(TAG, "Selected-parent attach could not start after discovery: %s", ot_error_to_string_(attach_error));
          if (attach_error == OT_ERROR_NOT_IMPLEMENTED) {
            this->active_ = false;
            this->phase_ = SwitchPhase::IDLE;
            this->set_status_(Status::API_MISSING);
            return;
          }
        } else {
          ESP_LOGW(TAG, "Preferred parent %s was not observed during discovery attempt %u/%u",
                   this->target_to_string_().c_str(), this->attempts_, this->max_attempts_);
        }

        this->phase_deadline_ms_ = 0;
        this->early_attach_pending_ = false;
      }

      // No suitable target was acted on during the last window, so either try
      // again or fail after the configured retry budget is exhausted.
      if (this->attempts_ >= this->max_attempts_) {
        this->dump_buffered_parent_responses_("failure replay", ReplayMode::INFO_ALL);
        ESP_LOGW(TAG, "Attach result: failed after %u discovery attempts; %s was not selected",
                 this->attempts_, this->target_to_string_().c_str());
        this->active_ = false;
        this->phase_ = SwitchPhase::IDLE;
        this->clear_preferred_parent_in_ot_(instance);
        this->set_status_(Status::FAILED);
        return;
      }

      // Begin another non-disruptive Parent Request sweep.
      this->attempts_++;
      this->target_observed_this_attempt_ = false;
      std::memset(&this->observed_target_extaddr_, 0, sizeof(this->observed_target_extaddr_));
      this->reset_parent_response_tracking_();

      const otDeviceRole role = otThreadGetDeviceRole(instance);
      // Capture the current role/parent before starting discovery so the logs
      // show what the node was attached to at the beginning of each attempt.
      ESP_LOGI(TAG, "Thread role before discovery: %s", device_role_to_string_(role));
      otRouterInfo current_parent{};
      if (otThreadGetParentInfo(instance, &current_parent) == OT_ERROR_NONE) {
        ESP_LOGI(TAG, "Current parent before discovery: RLOC16 0x%04x ExtAddr %s", current_parent.mRloc16,
                 this->extaddr_to_string_(current_parent.mExtAddress).c_str());
      } else {
        ESP_LOGI(TAG, "Current parent before discovery: none");
      }

      ESP_LOGI(TAG, "Parent discovery attempt %u/%u for %s", this->attempts_, this->max_attempts_,
               this->target_to_string_().c_str());
      otError discovery_error = this->start_parent_discovery_(instance);
      if (discovery_error == OT_ERROR_NONE) {
        // When early attach is not already armed by a just-arrived target
        // response, keep the standard discovery window open for the configured
        // retry interval.
        if (!this->early_attach_pending_) {
          this->phase_deadline_ms_ = now + this->retry_interval_ms_;
        }
        this->set_status_(Status::DISCOVERING);
        return;
      }

      ESP_LOGW(TAG, "Parent discovery could not start: %s", ot_error_to_string_(discovery_error));
      if (discovery_error == OT_ERROR_NOT_IMPLEMENTED) {
        this->active_ = false;
        this->phase_ = SwitchPhase::IDLE;
        this->set_status_(Status::API_MISSING);
      } else if (discovery_error == OT_ERROR_BUSY) {
        this->phase_deadline_ms_ = now + this->retry_interval_ms_;
        this->set_status_(Status::BUSY);
      } else {
        this->phase_deadline_ms_ = now + this->retry_interval_ms_;
      }
      return;
    }

    case SwitchPhase::ATTACHING:
      // While attaching, only act once the selected-parent deadline expires or
      // the current parent changes to the requested target.
      if (this->phase_deadline_ms_ != 0 && static_cast<int32_t>(now - this->phase_deadline_ms_) < 0) {
        return;
      }
      {
        // The attach request was launched, but the device never actually moved
        // to the requested parent before the timeout expired.
        const otDeviceRole role = otThreadGetDeviceRole(instance);
        otRouterInfo parent_info{};
        if (otThreadGetParentInfo(instance, &parent_info) == OT_ERROR_NONE) {
          ESP_LOGW(TAG,
                   "Attach result: timed out after %lu ms for %s; role=%s current_parent=0x%04x/%s; returning to discovery",
                   static_cast<unsigned long>(now - this->attach_start_ms_), this->target_to_string_().c_str(),
                   device_role_to_string_(role), parent_info.mRloc16,
                   this->extaddr_to_string_(parent_info.mExtAddress).c_str());
        } else {
          ESP_LOGW(TAG, "Attach result: timed out after %lu ms for %s; role=%s current_parent=none; returning to discovery",
                   static_cast<unsigned long>(now - this->attach_start_ms_), this->target_to_string_().c_str(),
                   device_role_to_string_(role));
        }
      }
      this->phase_ = SwitchPhase::DISCOVERING;
      this->phase_deadline_ms_ = 0;
      this->set_status_(Status::DISCOVERING);
      return;

    case SwitchPhase::IDLE:
      return;
  }
}

/**
 * Forward a Parent Response callback from the C hook into the component.
 *
 * @param info Parsed Parent Response information from OpenThread.
 * @param context Opaque pointer to the owning component instance.
 */
void ThreadPreferredParentComponent::parent_response_callback_(const otThreadParentResponseInfo *info, void *context) {
  // The C callback exported by the patch forwards back into the owning C++
  // component instance. A null context means registration never completed.
  if (context == nullptr) {
    return;
  }
  static_cast<ThreadPreferredParentComponent *>(context)->handle_parent_response_(info);
}

/**
 * Check whether a Parent Response matches the configured target.
 *
 * @param info Parent Response information to inspect.
 * @return `true` when `info` identifies the requested parent.
 */
bool ThreadPreferredParentComponent::parent_response_matches_target_(const otThreadParentResponseInfo &info) const {
  // Match using whichever identifier form the user configured. Discovery may
  // observe many candidates, but only one of them is considered the target.
  switch (this->target_type_) {
    case TargetType::RLOC16:
      return info.mRloc16 == this->target_rloc16_;
    case TargetType::EXTADDR:
      return this->extaddr_matches_(info.mExtAddr, this->target_extaddr_);
    case TargetType::NONE:
      return false;
  }
  return false;
}

/**
 * Record and react to a Parent Response observed during discovery.
 *
 * @param info Parsed Parent Response information from OpenThread.
 */
void ThreadPreferredParentComponent::handle_parent_response_(const otThreadParentResponseInfo *info) {
  if (info == nullptr) {
    return;
  }

  // Every Parent Response is tracked so discovery behavior can be summarized
  // even when the live log is running at a lower verbosity.
  this->parent_response_count_++;
  const bool target_match = this->parent_response_matches_target_(*info);

  if (target_match) {
    const bool first_target_response = !this->target_observed_this_attempt_;
    this->target_observed_this_attempt_ = true;
    this->observed_target_extaddr_ = info->mExtAddr;
    this->parent_response_target_count_++;
    if (!this->best_target_rssi_valid_ || info->mRssi > this->best_target_rssi_) {
      this->best_target_rssi_valid_ = true;
      this->best_target_rssi_ = info->mRssi;
      this->best_target_rloc16_ = info->mRloc16;
    }

    if (first_target_response) {
      // When enabled, shorten the discovery window once the requested parent is
      // observed so attach can begin quickly without waiting for the full retry
      // interval to elapse.
      const uint32_t observed_ms = millis() - this->current_attempt_start_ms_;
      this->discovery_target_observed_ms_ = observed_ms;
      if (this->active_ && this->phase_ == SwitchPhase::DISCOVERING && this->early_attach_on_target_ &&
          !this->early_attach_pending_) {
        this->early_attach_pending_ = true;
        this->phase_deadline_ms_ = millis() + this->early_attach_delay_ms_;
        ESP_LOGI(TAG,
                 "Target Parent Response observed after %lu ms during discovery; early selected-parent attach scheduled in %u ms",
                 static_cast<unsigned long>(observed_ms), this->early_attach_delay_ms_);
      }
    }
  }

  const uint32_t now = millis();
  // Preserve the latest responses in sequence order so timeout and failure logs
  // can replay what discovery actually saw, even if the live VV log was off.
  BufferedParentResponse &entry = this->parent_response_buffer_[this->parent_response_buffer_head_];
  entry.valid = true;
  entry.sequence = this->parent_response_count_;
  entry.timestamp_ms = now - this->current_attempt_start_ms_;
  entry.info = *info;
  entry.target_match = target_match;
  this->parent_response_buffer_head_ = (this->parent_response_buffer_head_ + 1) % PARENT_RESPONSE_BUFFER_SIZE;

  if (this->log_parent_responses_) {
    this->log_parent_response_vv_(entry, "live");
  }
}

/**
 * Log one buffered Parent Response at INFO level.
 *
 * @param entry Buffered Parent Response entry to log.
 * @param prefix Prefix describing the replay or live-log context.
 */
void ThreadPreferredParentComponent::log_parent_response_info_(const BufferedParentResponse &entry, const char *prefix) const {
  const otThreadParentResponseInfo &info = entry.info;
  ESP_LOGI(TAG,
           "Parent Response %s #%lu attempt_t+%lums: ExtAddr %s RLOC16 0x%04x RSSI %d priority %d "
           "LQ3/LQ2/LQ1 %u/%u/%u device_attached=%s target_match=%s",
           prefix, static_cast<unsigned long>(entry.sequence), static_cast<unsigned long>(entry.timestamp_ms),
           this->extaddr_to_string_(info.mExtAddr).c_str(), info.mRloc16, info.mRssi, info.mPriority,
           info.mLinkQuality3, info.mLinkQuality2, info.mLinkQuality1, YESNO(info.mIsAttached),
           YESNO(entry.target_match));
}

/**
 * Log one buffered Parent Response at very-verbose level.
 *
 * @param entry Buffered Parent Response entry to log.
 * @param prefix Prefix describing the replay or live-log context.
 */
void ThreadPreferredParentComponent::log_parent_response_vv_(const BufferedParentResponse &entry, const char *prefix) const {
  const otThreadParentResponseInfo &info = entry.info;
  ESP_LOGVV(TAG,
            "Parent Response %s #%lu attempt_t+%lums: ExtAddr %s RLOC16 0x%04x RSSI %d priority %d "
            "LQ3/LQ2/LQ1 %u/%u/%u device_attached=%s target_match=%s",
            prefix, static_cast<unsigned long>(entry.sequence), static_cast<unsigned long>(entry.timestamp_ms),
            this->extaddr_to_string_(info.mExtAddr).c_str(), info.mRloc16, info.mRssi, info.mPriority,
            info.mLinkQuality3, info.mLinkQuality2, info.mLinkQuality1, YESNO(info.mIsAttached),
            YESNO(entry.target_match));
}

/**
 * Log a one-line summary of the current discovery attempt.
 *
 * @param reason Short label describing why the summary is being emitted.
 */
void ThreadPreferredParentComponent::log_discovery_summary_(const char *reason) const {
  // Summarize discovery at INFO level so normal logs still reveal whether the
  // requested target was ever seen and, if so, how strong its best RSSI was.
  if (this->best_target_rssi_valid_) {
    ESP_LOGI(TAG,
             "Discovery summary (%s): %lu Parent Responses, %lu target match(es), best target RLOC16 0x%04x RSSI %d",
             reason, static_cast<unsigned long>(this->parent_response_count_),
             static_cast<unsigned long>(this->parent_response_target_count_), this->best_target_rloc16_,
             this->best_target_rssi_);
  } else {
    ESP_LOGI(TAG, "Discovery summary (%s): %lu Parent Responses, 0 target matches", reason,
             static_cast<unsigned long>(this->parent_response_count_));
  }
}

/**
 * Replay buffered Parent Responses into the log.
 *
 * @param reason Short label describing why the replay is being emitted.
 * @param mode Controls whether all responses or only target matches are shown.
 */
void ThreadPreferredParentComponent::dump_buffered_parent_responses_(const char *reason, ReplayMode mode) {
  if (!this->log_parent_responses_) {
    return;
  }

  // Avoid replaying the same buffered responses multiple times across repeated
  // timeout/failure/success paths.
  if (this->parent_response_count_ == this->parent_response_last_dumped_count_) {
    ESP_LOGI(TAG, "Parent Response replay (%s): no new responses", reason);
    return;
  }

  const bool target_only = mode == ReplayMode::INFO_TARGET_ONLY;
  uint32_t shown_count = 0;

  const uint32_t first_sequence =
      this->parent_response_count_ > PARENT_RESPONSE_BUFFER_SIZE ? this->parent_response_count_ - PARENT_RESPONSE_BUFFER_SIZE + 1 : 1;

  // First count what would be shown so the replay header can describe the
  // output accurately before the detailed entries are emitted.
  for (uint32_t sequence = first_sequence; sequence <= this->parent_response_count_; sequence++) {
    for (const auto &entry : this->parent_response_buffer_) {
      if (!entry.valid || entry.sequence != sequence || entry.sequence <= this->parent_response_last_dumped_count_) {
        continue;
      }
      if (target_only && !entry.target_match) {
        continue;
      }
      shown_count++;
    }
  }

  if (shown_count == 0) {
    ESP_LOGI(TAG, "Parent Response replay (%s): no matching responses to show", reason);
    this->parent_response_last_dumped_count_ = this->parent_response_count_;
    return;
  }

  ESP_LOGI(TAG, "Parent Response replay (%s): showing %lu buffered response(s)", reason,
           static_cast<unsigned long>(shown_count));

  for (uint32_t sequence = first_sequence; sequence <= this->parent_response_count_; sequence++) {
    for (const auto &entry : this->parent_response_buffer_) {
      if (!entry.valid || entry.sequence != sequence || entry.sequence <= this->parent_response_last_dumped_count_) {
        continue;
      }
      if (target_only && !entry.target_match) {
        continue;
      }
      this->log_parent_response_info_(entry, "replay");
    }
  }

  this->parent_response_last_dumped_count_ = this->parent_response_count_;
}

/**
 * Check whether the current Thread role is child.
 *
 * @param instance Active OpenThread instance.
 * @return `true` when the device is currently attached as a child.
 */
bool ThreadPreferredParentComponent::is_child_(otInstance *instance) const {
  return otThreadGetDeviceRole(instance) == OT_DEVICE_ROLE_CHILD;
}

/**
 * Check whether the current OpenThread parent matches the requested target.
 *
 * @param instance Active OpenThread instance.
 * @return `true` when the current parent is the preferred parent target.
 */
bool ThreadPreferredParentComponent::current_parent_matches_(otInstance *instance) const {
  otRouterInfo parent_info{};
  if (otThreadGetParentInfo(instance, &parent_info) != OT_ERROR_NONE) {
    return false;
  }

  switch (this->target_type_) {
    case TargetType::RLOC16:
      return parent_info.mRloc16 == this->target_rloc16_;
    case TargetType::EXTADDR:
      return this->extaddr_matches_(parent_info.mExtAddress, this->target_extaddr_);
    case TargetType::NONE:
      return false;
  }
  return false;
}

/**
 * Start a Parent Request discovery pass for the configured target.
 *
 * @param instance Active OpenThread instance.
 * @return OpenThread status describing whether discovery started successfully.
 */
otError ThreadPreferredParentComponent::start_parent_discovery_(otInstance *instance) {
  if (this->parent_request_unicast_) {
    otExtAddress selected{};

    if (this->target_type_ == TargetType::EXTADDR) {
      // ExtAddr targets can be used directly for unicast Parent Requests.
      selected = this->target_extaddr_;
    } else if (this->target_type_ == TargetType::RLOC16) {
      // Unicast discovery still needs an ExtAddr on the wire, so resolve the
      // configured RLOC16 against the current neighbor table first.
      if (!this->resolve_rloc16_to_extaddr_(instance, this->target_rloc16_, &selected)) {
        ESP_LOGW(TAG, "Parent Request unicast needs ExtAddr, but RLOC16 0x%04x is not resolved; falling back to multicast discovery",
                 this->target_rloc16_);
        selected = otExtAddress{};
      }
    } else {
      return OT_ERROR_INVALID_ARGS;
    }

    if (thread_preferred_parent_ot_start_parent_discovery_unicast != nullptr &&
        !this->extaddr_matches_(selected, otExtAddress{})) {
      ESP_LOGI(TAG, "Starting non-disruptive unicast Parent Request discovery to ExtAddr %s for %s",
               this->extaddr_to_string_(selected).c_str(), this->target_to_string_().c_str());
      return this->start_parent_discovery_unicast_(instance, selected);
    }

    if (thread_preferred_parent_ot_start_parent_discovery_unicast == nullptr) {
      ESP_LOGW(TAG, "Unicast Parent Request discovery hook missing; falling back to multicast discovery");
    }
  }

  if (thread_preferred_parent_ot_start_parent_discovery != nullptr) {
    ESP_LOGI(TAG, "Starting non-disruptive multicast Parent Request discovery for %s", this->target_to_string_().c_str());
    return thread_preferred_parent_ot_start_parent_discovery(instance);
  }

  // Final fallback: use OpenThread's public better-parent search API. This can
  // be disruptive because upstream OpenThread is free to continue into a real
  // reattach flow, so the patched discovery-only hooks are preferred whenever
  // they are available.
  ESP_LOGW(TAG, "Discovery-only OpenThread hook missing; falling back to otThreadSearchForBetterParent()");
  return otThreadSearchForBetterParent(instance);
}

/**
 * Start a unicast Parent Request discovery pass to a specific extended address.
 *
 * @param instance Active OpenThread instance.
 * @param extaddr Preferred parent extended address.
 * @return OpenThread status describing whether discovery started successfully.
 */
otError ThreadPreferredParentComponent::start_parent_discovery_unicast_(otInstance *instance, const otExtAddress &extaddr) {
  // Kept as a tiny wrapper so the rest of the component can treat the weak
  // symbol like a normal method call with a consistent OT_ERROR contract.
  if (thread_preferred_parent_ot_start_parent_discovery_unicast == nullptr) {
    return OT_ERROR_NOT_IMPLEMENTED;
  }
  return thread_preferred_parent_ot_start_parent_discovery_unicast(instance, &extaddr);
}

/**
 * Start a targeted selected-parent attach for the configured target.
 *
 * @param instance Active OpenThread instance.
 * @return OpenThread status describing whether attach startup succeeded.
 */
otError ThreadPreferredParentComponent::start_selected_parent_attach_(otInstance *instance) {
  otExtAddress selected{};

  if (this->target_observed_this_attempt_) {
    // Prefer the address observed in the live Parent Response because it is
    // guaranteed to match what discovery just saw on the network.
    selected = this->observed_target_extaddr_;
  } else if (this->target_type_ == TargetType::EXTADDR) {
    // If no live observation was captured, fall back to the configured target.
    selected = this->target_extaddr_;
  } else if (!this->resolve_rloc16_to_extaddr_(instance, this->target_rloc16_, &selected)) {
    ESP_LOGW(TAG, "RLOC16 0x%04x is not resolved to an ExtAddr", this->target_rloc16_);
    return OT_ERROR_NOT_FOUND;
  }

  if (this->selected_parent_hook_available_()) {
    // Prefer the custom selected-parent bridge because it directly constrains
    // the attach attempt to the observed or configured target ExtAddr.
    ESP_LOGI(TAG, "Starting selected-parent attach to ExtAddr %s", this->extaddr_to_string_(selected).c_str());
    const bool accepted = this->request_selected_parent_attach_(instance, selected);
    ESP_LOGI(TAG, "Selected-parent attach hook returned %s", YESNO(accepted));
    return accepted ? OT_ERROR_NONE : OT_ERROR_FAILED;
  }

  if (this->require_selected_parent_hook_) {
    // In strict mode, do not silently degrade to older APIs because they may
    // not preserve the intended targeted-attach semantics.
    return OT_ERROR_NOT_IMPLEMENTED;
  }

  switch (this->target_type_) {
    case TargetType::EXTADDR:
      // Older patch variants exposed different helper symbols; try the most
      // specific ones first, then fall back to the generic better-parent flow.
      if (otThreadSearchForPreferredParentExtAddress != nullptr) {
        return otThreadSearchForPreferredParentExtAddress(instance, &this->target_extaddr_);
      }
      if (otThreadSetPreferredParentExtAddress != nullptr) {
        otError error = otThreadSetPreferredParentExtAddress(instance, &this->target_extaddr_);
        if (error != OT_ERROR_NONE) {
          return error;
        }
        return otThreadSearchForBetterParent(instance);
      }
      break;
    case TargetType::RLOC16:
      // RLOC16 support historically existed under several helper names across
      // experiments, so probe them in most-specific-to-most-generic order.
      if (otThreadSearchForPreferredParentRloc16 != nullptr) {
        return otThreadSearchForPreferredParentRloc16(instance, this->target_rloc16_);
      }
      if (otThreadSearchForPreferredParent != nullptr) {
        return otThreadSearchForPreferredParent(instance, this->target_rloc16_);
      }
      if (otThreadSetPreferredParentRloc16 != nullptr) {
        otError error = otThreadSetPreferredParentRloc16(instance, this->target_rloc16_);
        if (error != OT_ERROR_NONE) {
          return error;
        }
        return otThreadSearchForBetterParent(instance);
      }
      break;
    case TargetType::NONE:
      return OT_ERROR_INVALID_ARGS;
  }

  return OT_ERROR_NOT_IMPLEMENTED;
}

/**
 * Check whether any selected-parent attach hook is available.
 *
 * @return `true` when a compatible selected-parent hook symbol is present.
 */
bool ThreadPreferredParentComponent::selected_parent_hook_available_() const {
  // Support both this component's native symbol and the older biparental
  // variant so the repo can coexist with earlier patch experiments.
  return thread_preferred_parent_ot_request_selected_parent_attach != nullptr ||
         biparental_ot_request_selected_parent_attach != nullptr;
}

/**
 * Check whether the multicast discovery-only hook is available.
 *
 * @return `true` when the discovery-only hook symbol is present.
 */
bool ThreadPreferredParentComponent::discovery_hook_available_() const {
  return thread_preferred_parent_ot_start_parent_discovery != nullptr;
}

/**
 * Check whether the unicast discovery-only hook is available.
 *
 * @return `true` when the unicast discovery hook symbol is present.
 */
bool ThreadPreferredParentComponent::discovery_unicast_hook_available_() const {
  return thread_preferred_parent_ot_start_parent_discovery_unicast != nullptr;
}

/**
 * Invoke the best available selected-parent attach hook.
 *
 * @param instance Active OpenThread instance.
 * @param extaddr Preferred parent extended address.
 * @return `true` when the hook accepted the attach request.
 */
bool ThreadPreferredParentComponent::request_selected_parent_attach_(otInstance *instance, const otExtAddress &extaddr) const {
  // Prefer the native symbol name, but fall back to the compatibility hook
  // when running against an environment that already carries the biparental
  // patch instead of this component's exact bridge symbol.
  if (thread_preferred_parent_ot_request_selected_parent_attach != nullptr) {
    return thread_preferred_parent_ot_request_selected_parent_attach(instance, &extaddr);
  }
  if (biparental_ot_request_selected_parent_attach != nullptr) {
    return biparental_ot_request_selected_parent_attach(instance, &extaddr);
  }
  return false;
}

/**
 * Resolve a neighbor RLOC16 into its extended address.
 *
 * @param instance Active OpenThread instance.
 * @param rloc16 Neighbor RLOC16 to resolve.
 * @param out Output buffer for the resolved extended address.
 * @return `true` when the RLOC16 was found in the current neighbor table.
 */
bool ThreadPreferredParentComponent::resolve_rloc16_to_extaddr_(otInstance *instance, uint16_t rloc16, otExtAddress *out) const {
  otNeighborInfoIterator iterator = OT_NEIGHBOR_INFO_ITERATOR_INIT;
  otNeighborInfo neighbor_info{};

  // OpenThread does not expose a direct "RLOC16 to ExtAddr" lookup here, so
  // walk the current neighbor table and return the first matching entry.
  while (otThreadGetNextNeighborInfo(instance, &iterator, &neighbor_info) == OT_ERROR_NONE) {
    if (neighbor_info.mRloc16 == rloc16) {
      *out = neighbor_info.mExtAddress;
      return true;
    }
  }

  return false;
}

/**
 * Clear any preferred-parent hints stored inside the OpenThread instance.
 *
 * @param instance Active OpenThread instance.
 */
void ThreadPreferredParentComponent::clear_preferred_parent_in_ot_(otInstance *instance) {
  // Different patch generations exported different cleanup helpers. Calling
  // whichever one is available keeps follow-up runs from inheriting stale
  // preferred-parent hints inside the OpenThread instance.
  if (otThreadClearPreferredParent != nullptr) {
    otThreadClearPreferredParent(instance);
    return;
  }

  if (otThreadClearPreferredParentRloc16 != nullptr) {
    otThreadClearPreferredParentRloc16(instance);
  }
}


/**
 * Parse a hexadecimal RLOC16 string.
 *
 * @param text Text to parse.
 * @param out Output buffer for the parsed RLOC16.
 * @return `true` when `text` contains a valid 16-bit hexadecimal value.
 */
bool ThreadPreferredParentComponent::parse_rloc16_(const std::string &text, uint16_t *out) {
  if (out == nullptr) {
    return false;
  }

  const size_t first = text.find_first_not_of(" \t\r\n");
  if (first == std::string::npos) {
    return false;
  }
  const size_t last = text.find_last_not_of(" \t\r\n");

  size_t pos = first;
  // Accept both bare hex and 0x-prefixed forms for convenience in YAML and
  // runtime service calls.
  if ((last - pos + 1) >= 2 && text[pos] == '0' && (text[pos + 1] == 'x' || text[pos + 1] == 'X')) {
    pos += 2;
  }

  if (pos > last) {
    return false;
  }

  uint32_t value = 0;
  size_t digits = 0;
  for (size_t i = pos; i <= last; i++) {
    const int nibble = hex_to_nibble_(text[i]);
    if (nibble < 0) {
      return false;
    }
    digits++;
    if (digits > 4) {
      return false;
    }
    value = (value << 4) | static_cast<uint32_t>(nibble);
  }

  if (digits == 0 || value > 0xFFFD) {
    return false;
  }

  *out = static_cast<uint16_t>(value);
  return true;
}

/**
 * Parse an extended address string into an `otExtAddress`.
 *
 * @param text Text to parse.
 * @param out Output buffer for the parsed extended address.
 * @return `true` when `text` contains a valid IEEE 802.15.4 extended address.
 */
bool ThreadPreferredParentComponent::parse_extaddr_(const std::string &text, otExtAddress *out) const {
  char hex[16];
  size_t count = 0;
  size_t start = 0;

  // Accept optional 0x prefix plus common separators so users can pass the
  // address in whichever notation they already have available.
  if (text.size() >= 2 && text[0] == '0' && (text[1] == 'x' || text[1] == 'X')) {
    start = 2;
  }

  for (size_t i = start; i < text.size(); i++) {
    const char c = text[i];
    if (c == ':' || c == '-' || c == '_' || c == ' ') {
      continue;
    }
    if (hex_to_nibble_(c) < 0 || count >= sizeof(hex)) {
      return false;
    }
    hex[count++] = c;
  }

  if (count != sizeof(hex)) {
    return false;
  }

  // Convert the normalized 16 hex digits into the 8-byte ExtAddr structure.
  for (size_t i = 0; i < sizeof(out->m8); i++) {
    int high = hex_to_nibble_(hex[i * 2]);
    int low = hex_to_nibble_(hex[i * 2 + 1]);
    if (high < 0 || low < 0) {
      return false;
    }
    out->m8[i] = static_cast<uint8_t>((high << 4) | low);
  }

  return true;
}

/**
 * Compare two extended addresses for equality.
 *
 * @param a First extended address.
 * @param b Second extended address.
 * @return `true` when `a` and `b` are byte-for-byte equal.
 */
bool ThreadPreferredParentComponent::extaddr_matches_(const otExtAddress &a, const otExtAddress &b) const {
  // otExtAddress is a plain 8-byte value type, so a byte-for-byte comparison is
  // both sufficient and easier to reason about than per-field matching.
  return std::memcmp(a.m8, b.m8, sizeof(a.m8)) == 0;
}

/**
 * Convert an extended address to compact lowercase hexadecimal text.
 *
 * @param addr Extended address to format.
 * @return Lowercase hexadecimal string representation of `addr`.
 */
std::string ThreadPreferredParentComponent::extaddr_to_string_(const otExtAddress &addr) const {
  static const char *const hex = "0123456789abcdef";
  std::string out;
  out.reserve(16);
  // Use compact lowercase hex because it fits naturally into log lines and can
  // be fed back into the parser without any additional normalization.
  for (uint8_t byte : addr.m8) {
    out.push_back(hex[(byte >> 4) & 0x0f]);
    out.push_back(hex[byte & 0x0f]);
  }
  return out;
}

/**
 * Format the currently configured target for logging.
 *
 * @return Human-readable description of the configured target.
 */
std::string ThreadPreferredParentComponent::target_to_string_() const {
  char buffer[32];
  // Centralize target formatting so every log line describes the active target
  // in the same shape regardless of where it originates.
  switch (this->target_type_) {
    case TargetType::RLOC16:
      std::snprintf(buffer, sizeof(buffer), "RLOC16 0x%04x", this->target_rloc16_);
      return buffer;
    case TargetType::EXTADDR:
      return "ExtAddr " + this->extaddr_to_string_(this->target_extaddr_);
    case TargetType::NONE:
      return "none";
  }
  return "unknown";
}

/**
 * Update the component status and log real transitions.
 *
 * @param status New component status.
 */
void ThreadPreferredParentComponent::set_status_(Status status) {
  if (this->status_ != status) {
    // Log only real transitions to keep high-frequency loop iterations from
    // producing duplicate status chatter.
    this->status_ = status;
    ESP_LOGD(TAG, "Status changed to %s", status_to_string_(status));
  }
}

/**
 * Convert an OpenThread device role to a log-friendly string.
 *
 * @param role OpenThread device role.
 * @return String name for `role`.
 */
const char *ThreadPreferredParentComponent::device_role_to_string_(otDeviceRole role) {
  switch (role) {
    case OT_DEVICE_ROLE_DISABLED:
      return "disabled";
    case OT_DEVICE_ROLE_DETACHED:
      return "detached";
    case OT_DEVICE_ROLE_CHILD:
      return "child";
    case OT_DEVICE_ROLE_ROUTER:
      return "router";
    case OT_DEVICE_ROLE_LEADER:
      return "leader";
  }
  return "unknown";
}

/**
 * Convert an internal switch phase to a log-friendly string.
 *
 * @param phase Switch phase to format.
 * @return String name for `phase`.
 */
const char *ThreadPreferredParentComponent::phase_to_string_(SwitchPhase phase) {
  switch (phase) {
    case SwitchPhase::IDLE:
      return "idle";
    case SwitchPhase::DISCOVERING:
      return "discovering";
    case SwitchPhase::ATTACHING:
      return "attaching";
  }
  return "unknown";
}

/**
 * Convert a target type to a log-friendly string.
 *
 * @param type Target type to format.
 * @return String name for `type`.
 */
const char *ThreadPreferredParentComponent::target_type_to_string_(TargetType type) {
  switch (type) {
    case TargetType::NONE:
      return "none";
    case TargetType::RLOC16:
      return "rloc16";
    case TargetType::EXTADDR:
      return "extaddr";
  }
  return "unknown";
}

/**
 * Convert a component status to a log-friendly string.
 *
 * @param status Component status to format.
 * @return String name for `status`.
 */
const char *ThreadPreferredParentComponent::status_to_string_(Status status) {
  switch (status) {
    case Status::IDLE:
      return "idle";
    case Status::DISCOVERING:
      return "discovering parents";
    case Status::ATTACHING:
      return "selected-parent attach in progress";
    case Status::WAITING:
      return "waiting";
    case Status::SUCCESS:
      return "success";
    case Status::FAILED:
      return "failed";
    case Status::API_MISSING:
      return "OpenThread hook missing";
    case Status::NOT_CHILD:
      return "not child";
    case Status::BUSY:
      return "busy";
    case Status::INVALID_TARGET:
      return "invalid target";
    case Status::RLOC_UNRESOLVED:
      return "rloc16 not resolved to extaddr";
  }
  return "unknown";
}

/**
 * Convert an OpenThread error code to a log-friendly string.
 *
 * @param error OpenThread error code.
 * @return String name for `error`.
 */
const char *ThreadPreferredParentComponent::ot_error_to_string_(otError error) {
  switch (error) {
    case OT_ERROR_NONE:
      return "OT_ERROR_NONE";
    case OT_ERROR_FAILED:
      return "OT_ERROR_FAILED";
    case OT_ERROR_INVALID_ARGS:
      return "OT_ERROR_INVALID_ARGS";
    case OT_ERROR_INVALID_STATE:
      return "OT_ERROR_INVALID_STATE";
    case OT_ERROR_NO_BUFS:
      return "OT_ERROR_NO_BUFS";
    case OT_ERROR_BUSY:
      return "OT_ERROR_BUSY";
    case OT_ERROR_NOT_FOUND:
      return "OT_ERROR_NOT_FOUND";
    case OT_ERROR_NOT_IMPLEMENTED:
      return "OT_ERROR_NOT_IMPLEMENTED";
    default:
      return "OT_ERROR_OTHER";
  }
}

/**
 * Convert one hexadecimal digit to its numeric nibble value.
 *
 * @param c Hexadecimal character to convert.
 * @return Nibble value in the range 0-15, or `-1` when `c` is invalid.
 */
int ThreadPreferredParentComponent::hex_to_nibble_(char c) {
  if (c >= '0' && c <= '9') {
    return c - '0';
  }
  if (c >= 'a' && c <= 'f') {
    return c - 'a' + 10;
  }
  if (c >= 'A' && c <= 'F') {
    return c - 'A' + 10;
  }
  return -1;
}

}  // namespace thread_preferred_parent
}  // namespace esphome
