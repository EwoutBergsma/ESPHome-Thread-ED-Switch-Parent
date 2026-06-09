#!/usr/bin/env python3
"""Patch ESP-IDF's vendored OpenThread sources for preferred-parent support.

This script is placed inside the ESPHome external component and is registered
from components/thread_preferred_parent/__init__.py. It patches ESP-IDF's
vendored OpenThread core with a small C bridge:

    thread_preferred_parent_ot_request_selected_parent_attach(...)

The patcher intentionally uses tolerant regular expressions because ESPHome /
PlatformIO may install slightly different ESP-IDF/OpenThread revisions.

Current patch notes:
  * fix clean-build ordering: remove the legacy force-detach block before
    applying candidate preseed, so ESP-IDF/OpenThread 5.5.4 does not fail
    with a required preseed regex miss.
  * keep the node attached during targeted BetterParent/selected-parent attach;
    this matches the biparental targeted-attach boundary and avoids racing the
    selected attach against a generic detach/reattach to the old parent.
  * pre-seed the selected parent candidate before Attach(kSelectedParent) so
    OpenThread revisions that synchronously build the Parent Request see the
    requested ExtAddr immediately.
  * for selected-parent attach only, force the Child ID Request once a Parent
    Response from the target has populated mParentCandidate, bypassing generic
    "better parent" acceptance heuristics that can veto weaker-but-requested
    parents.
  * optionally expose unicast Parent Request discovery, so ESPHome can send the
    preflight Parent Request directly to the configured target ExtAddr instead
    of the all-routers multicast address.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path
from typing import Callable, Optional

DEFAULT_ROOT = Path.home() / ".platformio/packages/framework-espidf/components/openthread/openthread/src/core"
FRAMEWORK_RELATIVE_CORE = Path("framework-espidf/components/openthread/openthread/src/core")
MARKER = "THREAD_PREFERRED_PARENT_SELECTED_PARENT_HOOK"


def normalize_newlines(text: str) -> str:
    """Normalize file content to LF so patch matching is platform-independent."""
    return text.replace("\r\n", "\n")


def backup_once(path: Path) -> None:
    """Create a one-time sibling backup before the first in-place patch."""
    backup = path.with_suffix(path.suffix + ".thread-preferred-parent.bak")
    if not backup.exists():
        shutil.copy2(path, backup)


def write_if_changed(path: Path, old: str, new: str, *, dry_run: bool = False) -> str:
    """Write a patched file only when content actually changed."""
    if old == new:
        return "already"
    if not dry_run:
        backup_once(path)
        path.write_text(new)
    return "patched"


def restore_truncated_mle_cpp_if_needed(root: Path, *, dry_run: bool = False) -> None:
    """Recover from a previous over-broad regex patch that truncated mle.cpp.

    Older v15 builds could leave SendParentRequest() without its exit block and
    without the closing namespaces. When a .thread-preferred-parent.bak exists,
    restore that original file before applying the safer patch sequence.
    """
    path = root / "thread/mle.cpp"
    backup = path.with_suffix(path.suffix + ".thread-preferred-parent.bak")
    if not path.exists() or not backup.exists():
        return

    text = normalize_newlines(path.read_text())
    send_parent_idx = text.find("Mle::Attacher::SendParentRequest")
    send_parent_truncated = False
    if send_parent_idx >= 0:
        tail = text[send_parent_idx:]
        send_parent_truncated = "\nexit:" not in tail or "SendParentRequestDone" not in tail

    brace_delta = text.count("{") - text.count("}")
    missing_namespace_close = "} // namespace ot" not in text[-2000:]

    if send_parent_truncated or brace_delta > 2 or missing_namespace_close:
        print(f"[thread_preferred_parent][restore] restoring truncated mle.cpp from backup: {backup}")
        if not dry_run:
            shutil.copy2(backup, path)


def find_function_span(text: str, signature_pattern: str) -> Optional[tuple[int, int, int, int]]:
    """Return (signature_start, open_brace, close_brace, body_end)."""
    match = re.search(signature_pattern, text, flags=re.MULTILINE)
    if match is None:
        return None

    open_brace = text.find("{", match.end())
    if open_brace < 0:
        return None

    depth = 0
    i = open_brace
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (match.start(), open_brace, i, i + 1)
        i += 1
    return None


def replace_literal(path: Path, old: str, new: str, *, already: str, dry_run: bool = False) -> str:
    """Replace a single exact snippet while remaining idempotent."""
    text = normalize_newlines(path.read_text())
    old = normalize_newlines(old)
    new = normalize_newlines(new)

    if already in text or new in text:
        return "already"
    if old not in text:
        return "missing"
    return write_if_changed(path, text, text.replace(old, new, 1), dry_run=dry_run)


def replace_regex(
    path: Path,
    pattern: str,
    repl: str | Callable[[re.Match[str]], str],
    *,
    already: str,
    label: str,
    dry_run: bool = False,
) -> str:
    """Apply one regex-based patch and report whether it matched."""
    text = normalize_newlines(path.read_text())
    if already in text:
        return "already"

    new, count = re.subn(pattern, repl, text, count=1, flags=re.DOTALL | re.MULTILINE)
    if count == 0:
        print(f"[thread_preferred_parent][detail] no regex match for {label}")
        return "missing"
    return write_if_changed(path, text, new, dry_run=dry_run)


def patch_mle_hpp(root: Path, *, dry_run: bool = False) -> str:
    """Declare the selected-parent attach entry point in mle.hpp."""
    path = root / "thread/mle.hpp"
    declaration = """    /**
     * Starts targeted attach flow to a selected parent by extended address.
     *
     * This is an ESPHome Thread preferred-parent extension. It reuses
     * OpenThread's internal selected-parent attach mode.
     *
     * @param[in] aExtAddress Extended address of the selected parent candidate.
     *
     * @retval kErrorNone Successfully started selected-parent attach flow.
     * @retval kErrorInvalidState Thread is not attached as a child.
     * @retval kErrorBusy An attach process is already in progress.
     */
    Error AttachToSelectedParent(const Mac::ExtAddress &aExtAddress); // THREAD_PREFERRED_PARENT_SELECTED_PARENT_HOOK
"""
    return replace_literal(
        path,
        "    Error SearchForBetterParent(void);\n",
        "    Error SearchForBetterParent(void);\n\n" + declaration,
        already="AttachToSelectedParent(const Mac::ExtAddress &aExtAddress)",
        dry_run=dry_run,
    )


def patch_attach_method(root: Path, *, dry_run: bool = False) -> str:
    """Add Mle::AttachToSelectedParent() to mle.cpp."""
    path = root / "thread/mle.cpp"
    method = """
Error Mle::AttachToSelectedParent(const Mac::ExtAddress &aExtAddress)
{
    // THREAD_PREFERRED_PARENT_SELECTED_PARENT_HOOK
    Error error = kErrorNone;

    VerifyOrExit(IsChild(), error = kErrorInvalidState);
    VerifyOrExit(!IsAttaching(), error = kErrorBusy);

    mAttacher.Attach(kSelectedParent);
    mAttacher.GetParentCandidate().SetExtAddress(aExtAddress);

exit:
    return error;
}

"""

    pattern = (
        r"(Error\s+Mle::SearchForBetterParent\s*\(\s*void\s*\)\s*\{.*?\n\}\s*\n\s*)"
        r"(?=bool\s+Mle::IsAttached\s*\()"
    )
    return replace_regex(
        path,
        pattern,
        lambda m: m.group(1).rstrip() + "\n\n" + method,
        already="Mle::AttachToSelectedParent",
        label="Mle::SearchForBetterParent insertion point",
        dry_run=dry_run,
    )


def patch_attach_method_force_detach(root: Path, *, dry_run: bool = False) -> str:
    """Legacy patch: insert a detach before selected-parent attach.

    Newer revisions remove this behavior again, but the helper remains so older
    partially-patched trees can still be recognized or upgraded safely.
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "THREAD_PREFERRED_PARENT_FORCE_DETACH_BEFORE_ATTACH" in text:
        return "already"

    pattern = (
        r"(Error\s+Mle::AttachToSelectedParent\s*\(\s*const\s+Mac::ExtAddress\s*&\s*aExtAddress\s*\)\s*\{.*?"
        r"VerifyOrExit\s*\(\s*!IsAttaching\s*\(\s*\)\s*,\s*error\s*=\s*kErrorBusy\s*\)\s*;\s*)"
        r"(mAttacher\.Attach\s*\(\s*kSelectedParent\s*\)\s*;)"
    )

    def repl(m):
        """Run repl.

        Returns:
            Result for this helper function.
        """
        return (
            m.group(1)
            + "\n    // THREAD_PREFERRED_PARENT_FORCE_DETACH_BEFORE_ATTACH\n"
            + "    // Force the ED to leave its current parent before starting the\n"
            + "    // selected-parent attach flow. Without this, some OpenThread\n"
            + "    // revisions accept the request but remain attached to the old parent.\n"
            + "    (void)BecomeDetached();\n\n    "
            + m.group(2)
        )

    return replace_regex(
        path,
        pattern,
        repl,
        already="THREAD_PREFERRED_PARENT_FORCE_DETACH_BEFORE_ATTACH",
        label="selected-parent force detach before attach",
        dry_run=dry_run,
    )




def patch_attach_method_interrupt_discovery(root: Path, *, dry_run: bool = False) -> str:
    """Allow selected-parent attach to interrupt an active discovery-only pass.

    Early-attach can call AttachToSelectedParent() while SearchForBetterParent()
    is still in its Parent Request collection window. That is intentional: the
    ESPHome component has already observed the requested target and does not need
    to wait for the discovery-only timer. Treat this specific in-progress state
    as interruptible instead of returning kErrorBusy.
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "THREAD_PREFERRED_PARENT_INTERRUPT_DISCOVERY_BEFORE_ATTACH" in text:
        return "already"

    span = find_function_span(
        text,
        r"Error\s+Mle::AttachToSelectedParent\s*\(\s*const\s+Mac::ExtAddress\s*&\s*aExtAddress\s*\)",
    )
    if span is None:
        print("[thread_preferred_parent][detail] no AttachToSelectedParent function span found")
        return "missing"

    _sig_start, open_brace, close_brace, _body_end = span
    body = text[open_brace:close_brace]
    needle = "    VerifyOrExit(!IsAttaching(), error = kErrorBusy);"
    if needle not in body:
        print("[thread_preferred_parent][detail] no IsAttaching guard found in AttachToSelectedParent")
        return "missing"

    replacement = """    if (thread_preferred_parent_ot_parent_discovery_active)
    {
        // THREAD_PREFERRED_PARENT_INTERRUPT_DISCOVERY_BEFORE_ATTACH
        // A requested target was already observed by ESPHome. Interrupt the
        // discovery-only Parent Request window and immediately begin the
        // selected-parent attach flow instead of returning kErrorBusy.
        // Do not declare a local variable here: AttachToSelectedParent() uses
        // VerifyOrExit()/goto exit, and C++ rejects jumps across initialized
        // locals in this function on ESP-IDF/OpenThread builds.
        thread_preferred_parent_ot_parent_discovery_active = false;
        thread_preferred_parent_ot_parent_discovery_unicast = false;
    }
    else
    {
        VerifyOrExit(!IsAttaching(), error = kErrorBusy);
    }"""

    new_body = body.replace(needle, replacement, 1)
    new = text[:open_brace] + new_body + text[close_brace:]
    return write_if_changed(path, text, new, dry_run=dry_run)

def patch_selected_parent_destination(root: Path, *, dry_run: bool = False) -> str:
    """Patch only the Parent Request destination-selection block.

    v15 patched from AppendVersionTlv() to the next SendTo() anchor. On ESP-IDF
    5.5.4 that can be too broad if the file has already been partially patched,
    and it can leave SendParentRequest() without its exit block / namespace tail.

    This version replaces only the compact destination branch immediately before
    the final message->SendTo(destination) call.

    Important FTD fix:
    Inside Mle::Attacher, OpenThread's locator requires typed Get<Mle>(), not
    bare Get(). Bare Get() compiles out in MTD but breaks FTD builds.
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())

    span = find_function_span(
        text,
        r"void\s+Mle::Attacher::SendParentRequest\s*\(\s*ParentRequestType\s+aType\s*\)",
    )
    if span is None:
        print("[thread_preferred_parent][detail] no SendParentRequest function span found")
        return "missing"

    _sig_start, open_brace, close_brace, _body_end = span
    body = text[open_brace:close_brace]

    if "THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK" in body:
        # Repair older generated code that used bare Get() in the FTD branch.
        repaired_body = body.replace(
            "destination.SetToLinkLocalAddress(Get().mParent.GetExtAddress());",
            "destination.SetToLinkLocalAddress(Get<Mle>().mParent.GetExtAddress());",
        ).replace(
            "destination.SetToLinkLocalAddress(Get().mParentSearch.GetSelectedParent().GetExtAddress());",
            "destination.SetToLinkLocalAddress(Get<Mle>().mParentSearch.GetSelectedParent().GetExtAddress());",
        )

        if repaired_body != body:
            new_text = text[:open_brace] + repaired_body + text[close_brace:]
            return write_if_changed(path, text, new_text, dry_run=dry_run)

        # Treat as already applied only if the function still has its normal exit
        # block and final SendTo(). This avoids accepting a previously-truncated
        # v15 patch as valid.
        if "SuccessOrExit(error = message->SendTo(destination));" in body and "\nexit:" in body:
            return "already"

        print("[thread_preferred_parent][detail] existing SendParentRequest unicast hook looks incomplete")
        return "missing"

    replacement = """
    if (thread_preferred_parent_ot_parent_discovery_active && thread_preferred_parent_ot_parent_discovery_unicast)
    {
        // THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK
        Mac::ExtAddress discoveryTarget;
        discoveryTarget.Set(thread_preferred_parent_ot_parent_discovery_extaddr.m8);
        destination.SetToLinkLocalAddress(discoveryTarget);
    }
    else if (aType == kToSelectedRouter)
    {
#if OPENTHREAD_FTD && OPENTHREAD_CONFIG_PARENT_SEARCH_ENABLE
        TxMessage *messageToCurParent = static_cast<TxMessage *>(message->Clone());
        VerifyOrExit(messageToCurParent != nullptr, error = kErrorNoBufs);

        destination.SetToLinkLocalAddress(Get<Mle>().mParent.GetExtAddress());
        error = messageToCurParent->SendTo(destination);
        if (error != kErrorNone)
        {
            messageToCurParent->Free();
            ExitNow();
        }
        Log(kMessageSend, kTypeParentRequestToRouters, destination);

        destination.SetToLinkLocalAddress(Get<Mle>().mParentSearch.GetSelectedParent().GetExtAddress());
#else
        // THREAD_PREFERRED_PARENT_SELECTED_PARENT_HOOK
        // MTD builds do not have ParentSearch, but selected-parent attach still
        // stores the requested candidate in mParentCandidate.
        destination.SetToLinkLocalAddress(mParentCandidate.GetExtAddress());
#endif
    }
    else
    {
        destination.SetToLinkLocalAllRoutersMulticast();
    }
"""

    # Match the compact destination-selection branch immediately before the
    # final message send, rather than replacing a wider section of the function.
    #   #if OPENTHREAD_FTD && OPENTHREAD_CONFIG_PARENT_SEARCH_ENABLE
    #       if (aType == kToSelectedRouter) { ... } else
    #   #endif
    #       { destination.SetToLinkLocalAllRoutersMulticast(); }
    #   SuccessOrExit(error = message->SendTo(destination));
    pattern = (
        r"\n\s*#if\s+OPENTHREAD_FTD\s*&&\s*OPENTHREAD_CONFIG_PARENT_SEARCH_ENABLE\s*\n"
        r"\s*if\s*\(\s*aType\s*==\s*kToSelectedRouter\s*\)\s*\{.*?\n\s*\}\s*\n"
        r"\s*else\s*\n\s*#endif\s*\n"
        r"\s*\{\s*\n\s*destination\.SetToLinkLocalAllRoutersMulticast\s*\(\s*\)\s*;\s*\n\s*\}\s*"
        r"(?=\n\s*SuccessOrExit\s*\(\s*error\s*=\s*message->SendTo\s*\(\s*destination\s*\)\s*\)\s*;)"
    )

    new_body, count = re.subn(
        pattern,
        "\n" + replacement.rstrip(),
        body,
        count=1,
        flags=re.DOTALL | re.MULTILINE,
    )

    if count == 0:
        # Fallback: ESP-IDF/OpenThread variants where the selected-router branch
        # has already been edited but still uses the same final multicast block.
        pattern = (
            r"\n\s*if\s*\(\s*aType\s*==\s*kToSelectedRouter\s*\)\s*\{.*?\n\s*\}\s*\n"
            r"\s*else\s*\n\s*\{\s*\n\s*destination\.SetToLinkLocalAllRoutersMulticast\s*\(\s*\)\s*;\s*\n\s*\}\s*"
            r"(?=\n\s*SuccessOrExit\s*\(\s*error\s*=\s*message->SendTo\s*\(\s*destination\s*\)\s*\)\s*;)"
        )
        new_body, count = re.subn(
            pattern,
            "\n" + replacement.rstrip(),
            body,
            count=1,
            flags=re.DOTALL | re.MULTILINE,
        )

    if count == 0:
        print("[thread_preferred_parent][detail] no safe destination-selection block match in SendParentRequest")
        return "missing"

    new_text = text[:open_brace] + new_body + text[close_brace:]
    return write_if_changed(path, text, new_text, dry_run=dry_run)

def patch_accept_selected_parent_without_current_parent_response(root: Path, *, dry_run: bool = False) -> str:
    """Patch accept selected parent without current parent response in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"

    replacement = """case kAnyPartition:
        case kSamePartition:
        case kDowngradeToReed:
        case kBetterParent:

            // Ensure that a Parent Response was received from the
            // current parent to which the device is attached, so
            // that the new parent candidate can be compared with the
            // current parent and confirmed to be preferred.
            VerifyOrExit(mReceivedResponseFromParent);
            break;

        case kSelectedParent:
            // THREAD_PREFERRED_PARENT_SELECTED_PARENT_HOOK
            // The attach flow is already constrained to the explicitly requested
            // candidate, so do not require a comparison response from the current
            // parent before sending the Child ID Request.
            break;"""

    pattern = (
        r"case\s+kAnyPartition\s*:\s*"
        r"case\s+kSamePartition\s*:\s*"
        r"case\s+kDowngradeToReed\s*:\s*"
        r"case\s+kBetterParent\s*:\s*"
        r"case\s+kSelectedParent\s*:\s*"
        r"//\s*Ensure\s+that\s+a\s+Parent\s+Response\s+was\s+received\s+from\s+the.*?"
        r"VerifyOrExit\s*\(\s*mReceivedResponseFromParent\s*\)\s*;\s*break\s*;"
    )
    return replace_regex(
        path,
        pattern,
        replacement,
        already="The attach flow is already constrained to the explicitly requested",
        label="selected-parent current-parent-response bypass",
        dry_run=dry_run,
    )


def patch_thread_api(root: Path, *, dry_run: bool = False) -> str:
    """Patch thread api in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "api/thread_api.cpp"
    bridge = """
using thread_preferred_parent_parent_response_callback_t = void (*)(const otThreadParentResponseInfo *aInfo, void *aContext);

static thread_preferred_parent_parent_response_callback_t sThreadPreferredParentParentResponseCallback = nullptr;
static void *sThreadPreferredParentParentResponseCallbackContext = nullptr;

extern "C" void thread_preferred_parent_ot_register_parent_response_callback(
    thread_preferred_parent_parent_response_callback_t aCallback,
    void *aContext)
{
    // THREAD_PREFERRED_PARENT_PARENT_RESPONSE_REPORTING_HOOK
    sThreadPreferredParentParentResponseCallback = aCallback;
    sThreadPreferredParentParentResponseCallbackContext = aContext;
}

extern "C" void thread_preferred_parent_ot_notify_parent_response(const otThreadParentResponseInfo *aInfo)
{
    // THREAD_PREFERRED_PARENT_PARENT_RESPONSE_REPORTING_HOOK
    if ((sThreadPreferredParentParentResponseCallback != nullptr) && (aInfo != nullptr))
    {
        sThreadPreferredParentParentResponseCallback(aInfo, sThreadPreferredParentParentResponseCallbackContext);
    }
}

extern "C" bool thread_preferred_parent_ot_parent_discovery_active = false;
extern "C" bool thread_preferred_parent_ot_parent_discovery_unicast = false;
extern "C" otExtAddress thread_preferred_parent_ot_parent_discovery_extaddr = {};

extern "C" otError thread_preferred_parent_ot_start_parent_discovery(otInstance *aInstance)
{
    // THREAD_PREFERRED_PARENT_DISCOVERY_ONLY_HOOK
    if (aInstance == nullptr)
    {
        return OT_ERROR_INVALID_ARGS;
    }

    thread_preferred_parent_ot_parent_discovery_active = true;
    thread_preferred_parent_ot_parent_discovery_unicast = false;
    otError error = AsCoreType(aInstance).Get<Mle::Mle>().SearchForBetterParent();
    if (error != OT_ERROR_NONE)
    {
        thread_preferred_parent_ot_parent_discovery_active = false;
        thread_preferred_parent_ot_parent_discovery_unicast = false;
    }
    return error;
}

extern "C" otError thread_preferred_parent_ot_start_parent_discovery_unicast(otInstance *aInstance,
                                                                               const otExtAddress *aPreferredExtAddress)
{
    // THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK
    if ((aInstance == nullptr) || (aPreferredExtAddress == nullptr))
    {
        return OT_ERROR_INVALID_ARGS;
    }

    thread_preferred_parent_ot_parent_discovery_extaddr = *aPreferredExtAddress;
    thread_preferred_parent_ot_parent_discovery_active = true;
    thread_preferred_parent_ot_parent_discovery_unicast = true;
    otError error = AsCoreType(aInstance).Get<Mle::Mle>().SearchForBetterParent();
    if (error != OT_ERROR_NONE)
    {
        thread_preferred_parent_ot_parent_discovery_active = false;
        thread_preferred_parent_ot_parent_discovery_unicast = false;
    }
    return error;
}

extern "C" bool thread_preferred_parent_ot_request_selected_parent_attach(otInstance *aInstance,
                                                                             const otExtAddress *aPreferredExtAddress)
{
    // THREAD_PREFERRED_PARENT_SELECTED_PARENT_HOOK
    if ((aInstance == nullptr) || (aPreferredExtAddress == nullptr))
    {
        return false;
    }

    return AsCoreType(aInstance).Get<Mle::Mle>().AttachToSelectedParent(AsCoreType(aPreferredExtAddress)) == kErrorNone;
}

extern "C" bool biparental_ot_request_selected_parent_attach(otInstance *aInstance,
                                                              const otExtAddress *aPreferredExtAddress)
{
    // THREAD_PREFERRED_PARENT_SELECTED_PARENT_HOOK
    // Compatibility alias for ESPHome-biparental-ED diagnostics/tools.
    return thread_preferred_parent_ot_request_selected_parent_attach(aInstance, aPreferredExtAddress);
}

"""
    pattern = (
        r"(otError\s+otThreadSearchForBetterParent\s*\(\s*otInstance\s*\*\s*aInstance\s*\)\s*"
        r"\{\s*return\s+AsCoreType\s*\(\s*aInstance\s*\)\s*\.\s*Get\s*<\s*Mle::Mle\s*>\s*\(\s*\)\s*\.\s*SearchForBetterParent\s*\(\s*\)\s*;\s*\}\s*)"
    )
    return replace_regex(
        path,
        pattern,
        lambda m: m.group(1).rstrip() + "\n\n" + bridge,
        already="thread_preferred_parent_ot_request_selected_parent_attach",
        label="otThreadSearchForBetterParent bridge insertion point",
        dry_run=dry_run,
    )


def patch_thread_api_parent_response_reporting(root: Path, *, dry_run: bool = False) -> str:
    """Patch thread api parent response reporting in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "api/thread_api.cpp"
    text = normalize_newlines(path.read_text())
    if "THREAD_PREFERRED_PARENT_PARENT_RESPONSE_REPORTING_HOOK" in text:
        return "already"

    addition = """
using thread_preferred_parent_parent_response_callback_t = void (*)(const otThreadParentResponseInfo *aInfo, void *aContext);

static thread_preferred_parent_parent_response_callback_t sThreadPreferredParentParentResponseCallback = nullptr;
static void *sThreadPreferredParentParentResponseCallbackContext = nullptr;

extern "C" void thread_preferred_parent_ot_register_parent_response_callback(
    thread_preferred_parent_parent_response_callback_t aCallback,
    void *aContext)
{
    // THREAD_PREFERRED_PARENT_PARENT_RESPONSE_REPORTING_HOOK
    sThreadPreferredParentParentResponseCallback = aCallback;
    sThreadPreferredParentParentResponseCallbackContext = aContext;
}

extern "C" void thread_preferred_parent_ot_notify_parent_response(const otThreadParentResponseInfo *aInfo)
{
    // THREAD_PREFERRED_PARENT_PARENT_RESPONSE_REPORTING_HOOK
    if ((sThreadPreferredParentParentResponseCallback != nullptr) && (aInfo != nullptr))
    {
        sThreadPreferredParentParentResponseCallback(aInfo, sThreadPreferredParentParentResponseCallbackContext);
    }
}

"""

    # If an earlier v4 patch already inserted the selected-parent bridge, place
    # the reporting functions immediately before it. Otherwise a fresh patch gets
    # the reporting bridge through patch_thread_api().
    pattern = r"(extern\s+\"C\"\s+bool\s+thread_preferred_parent_ot_request_selected_parent_attach\s*\()"
    return replace_regex(
        path,
        pattern,
        lambda m: addition + m.group(1),
        already="THREAD_PREFERRED_PARENT_PARENT_RESPONSE_REPORTING_HOOK",
        label="thread_api.cpp parent-response reporting bridge",
        dry_run=dry_run,
    )


def patch_thread_api_discovery_bridge(root: Path, *, dry_run: bool = False) -> str:
    """Patch thread api discovery bridge in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "api/thread_api.cpp"
    text = normalize_newlines(path.read_text())

    fresh_addition = """
extern \"C\" bool thread_preferred_parent_ot_parent_discovery_active = false;
extern \"C\" bool thread_preferred_parent_ot_parent_discovery_unicast = false;
extern \"C\" otExtAddress thread_preferred_parent_ot_parent_discovery_extaddr = {};

extern \"C\" otError thread_preferred_parent_ot_start_parent_discovery(otInstance *aInstance)
{
    // THREAD_PREFERRED_PARENT_DISCOVERY_ONLY_HOOK
    if (aInstance == nullptr)
    {
        return OT_ERROR_INVALID_ARGS;
    }

    thread_preferred_parent_ot_parent_discovery_active = true;
    thread_preferred_parent_ot_parent_discovery_unicast = false;
    otError error = AsCoreType(aInstance).Get<Mle::Mle>().SearchForBetterParent();
    if (error != OT_ERROR_NONE)
    {
        thread_preferred_parent_ot_parent_discovery_active = false;
        thread_preferred_parent_ot_parent_discovery_unicast = false;
    }
    return error;
}

extern \"C\" otError thread_preferred_parent_ot_start_parent_discovery_unicast(otInstance *aInstance,
                                                                               const otExtAddress *aPreferredExtAddress)
{
    // THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK
    if ((aInstance == nullptr) || (aPreferredExtAddress == nullptr))
    {
        return OT_ERROR_INVALID_ARGS;
    }

    thread_preferred_parent_ot_parent_discovery_extaddr = *aPreferredExtAddress;
    thread_preferred_parent_ot_parent_discovery_active = true;
    thread_preferred_parent_ot_parent_discovery_unicast = true;
    otError error = AsCoreType(aInstance).Get<Mle::Mle>().SearchForBetterParent();
    if (error != OT_ERROR_NONE)
    {
        thread_preferred_parent_ot_parent_discovery_active = false;
        thread_preferred_parent_ot_parent_discovery_unicast = false;
    }
    return error;
}

"""

    if "THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK" in text:
        return "already"

    if "THREAD_PREFERRED_PARENT_DISCOVERY_ONLY_HOOK" in text:
        new = text
        if "thread_preferred_parent_ot_parent_discovery_unicast" not in new:
            new = new.replace(
                'extern "C" bool thread_preferred_parent_ot_parent_discovery_active = false;',
                'extern "C" bool thread_preferred_parent_ot_parent_discovery_active = false;\n'
                'extern "C" bool thread_preferred_parent_ot_parent_discovery_unicast = false;\n'
                'extern "C" otExtAddress thread_preferred_parent_ot_parent_discovery_extaddr = {};',
                1,
            )
        new = new.replace(
            'thread_preferred_parent_ot_parent_discovery_active = true;\n    otError error = AsCoreType(aInstance).Get<Mle::Mle>().SearchForBetterParent();',
            'thread_preferred_parent_ot_parent_discovery_active = true;\n    thread_preferred_parent_ot_parent_discovery_unicast = false;\n    otError error = AsCoreType(aInstance).Get<Mle::Mle>().SearchForBetterParent();',
            1,
        )
        new = new.replace(
            'thread_preferred_parent_ot_parent_discovery_active = false;\n    }\n    return error;\n}',
            'thread_preferred_parent_ot_parent_discovery_active = false;\n        thread_preferred_parent_ot_parent_discovery_unicast = false;\n    }\n    return error;\n}',
            1,
        )
        unicast_fn = """

extern \"C\" otError thread_preferred_parent_ot_start_parent_discovery_unicast(otInstance *aInstance,
                                                                               const otExtAddress *aPreferredExtAddress)
{
    // THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK
    if ((aInstance == nullptr) || (aPreferredExtAddress == nullptr))
    {
        return OT_ERROR_INVALID_ARGS;
    }

    thread_preferred_parent_ot_parent_discovery_extaddr = *aPreferredExtAddress;
    thread_preferred_parent_ot_parent_discovery_active = true;
    thread_preferred_parent_ot_parent_discovery_unicast = true;
    otError error = AsCoreType(aInstance).Get<Mle::Mle>().SearchForBetterParent();
    if (error != OT_ERROR_NONE)
    {
        thread_preferred_parent_ot_parent_discovery_active = false;
        thread_preferred_parent_ot_parent_discovery_unicast = false;
    }
    return error;
}
"""
        pattern = (
            r'(extern\s+"C"\s+otError\s+thread_preferred_parent_ot_start_parent_discovery\s*\(\s*otInstance\s*\*\s*aInstance\s*\)\s*\{.*?\n\})'
        )
        new, count = re.subn(pattern, lambda m: m.group(1) + unicast_fn, new, count=1, flags=re.DOTALL | re.MULTILINE)
        if count == 0:
            print("[thread_preferred_parent][detail] no regex match for discovery-only bridge upgrade")
            return "missing"
        return write_if_changed(path, text, new, dry_run=dry_run)

    pattern = r"(extern\s+\"C\"\s+bool\s+thread_preferred_parent_ot_request_selected_parent_attach\s*\()"
    return replace_regex(
        path,
        pattern,
        lambda m: fresh_addition + m.group(1),
        already="THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK",
        label="thread_api.cpp discovery-only/unicast bridge",
        dry_run=dry_run,
    )
def patch_mle_parent_response_reporting_declaration(root: Path, *, dry_run: bool = False) -> str:
    """Patch mle parent response reporting declaration in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    namespace_idx = text.find("namespace ot")
    prefix = text if namespace_idx < 0 else text[:namespace_idx]

    required = [
        'extern "C" void thread_preferred_parent_ot_notify_parent_response(const otThreadParentResponseInfo *aInfo);',
        'extern "C" bool thread_preferred_parent_ot_parent_discovery_active;',
        'extern "C" bool thread_preferred_parent_ot_parent_discovery_unicast;',
        'extern "C" otExtAddress thread_preferred_parent_ot_parent_discovery_extaddr;',
    ]
    if all(item in prefix for item in required):
        return "already"

    include_old = '#include "utils/static_counter.hpp"\n'
    include_new = '#include "utils/static_counter.hpp"\n\n#include <openthread/thread.h>\n\nextern "C" void thread_preferred_parent_ot_notify_parent_response(const otThreadParentResponseInfo *aInfo);\nextern "C" bool thread_preferred_parent_ot_parent_discovery_active;\nextern "C" bool thread_preferred_parent_ot_parent_discovery_unicast;\nextern "C" otExtAddress thread_preferred_parent_ot_parent_discovery_extaddr;\n// THREAD_PREFERRED_PARENT_PARENT_RESPONSE_REPORTING_HOOK\n// THREAD_PREFERRED_PARENT_DISCOVERY_ONLY_HOOK\n// THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK\n'

    if required[0] in prefix:
        insertion = "\n".join(item for item in required[1:] if item not in prefix)
        if insertion:
            new_text = text.replace(required[0], required[0] + "\n" + insertion, 1)
            return write_if_changed(path, text, new_text, dry_run=dry_run)
        return "already"

    if include_old not in text:
        return "missing"
    return write_if_changed(path, text, text.replace(include_old, include_new, 1), dry_run=dry_run)

def patch_mle_discovery_declaration(root: Path, *, dry_run: bool = False) -> str:
    """Patch mle discovery declaration in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    namespace_idx = text.find("namespace ot")
    prefix = text if namespace_idx < 0 else text[:namespace_idx]

    active_decl = 'extern "C" bool thread_preferred_parent_ot_parent_discovery_active;'
    unicast_decl = 'extern "C" bool thread_preferred_parent_ot_parent_discovery_unicast;'
    extaddr_decl = 'extern "C" otExtAddress thread_preferred_parent_ot_parent_discovery_extaddr;'
    notify_decl = 'extern "C" void thread_preferred_parent_ot_notify_parent_response(const otThreadParentResponseInfo *aInfo);'

    if active_decl in prefix and unicast_decl in prefix and extaddr_decl in prefix:
        return "already"

    if active_decl in prefix:
        addition = ""
        if unicast_decl not in prefix:
            addition += "\n" + unicast_decl
        if extaddr_decl not in prefix:
            addition += "\n" + extaddr_decl + " // THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK"
        new_text = text.replace(active_decl, active_decl + addition, 1)
        return write_if_changed(path, text, new_text, dry_run=dry_run)

    if notify_decl in prefix:
        new_text = text.replace(
            notify_decl,
            notify_decl + "\n" + active_decl + "\n" + unicast_decl + "\n" + extaddr_decl + " // THREAD_PREFERRED_PARENT_DISCOVERY_UNICAST_HOOK",
            1,
        )
        return write_if_changed(path, text, new_text, dry_run=dry_run)
    return "missing"

def patch_mle_discovery_cancel(root: Path, *, dry_run: bool = False) -> str:
    """Patch mle discovery cancel in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "THREAD_PREFERRED_PARENT_DISCOVERY_ONLY_CANCEL" in text:
        if "thread_preferred_parent_ot_parent_discovery_unicast = false" in text:
            return "already"
        new = text.replace(
            'thread_preferred_parent_ot_parent_discovery_active = false;\n        SetState(kStateIdle);',
            'thread_preferred_parent_ot_parent_discovery_active = false;\n        thread_preferred_parent_ot_parent_discovery_unicast = false;\n        SetState(kStateIdle);',
            1,
        )
        return write_if_changed(path, text, new, dry_run=dry_run)

    pattern = (
        r"(void\s+Mle::Attacher::HandleTimer\s*\(\s*void\s*\)\s*\{\s*"
        r"uint32_t\s+delay\s*=\s*0\s*;\s*"
        r"bool\s+shouldAnnounce\s*=\s*true\s*;\s*"
        r"ParentRequestType\s+type\s*;\s*)"
    )
    block = """

    if (thread_preferred_parent_ot_parent_discovery_active && (mMode == kBetterParent) &&
        (mState == kStateParentRequest))
    {
        // THREAD_PREFERRED_PARENT_DISCOVERY_ONLY_CANCEL
        // The ESPHome component only wants the Parent Responses in this phase.
        // Do not proceed to Child ID Request or detach; keep the existing
        // parent until the target was actually observed.
        LogNote(\"ThreadPreferredParent discovery window complete\");
        thread_preferred_parent_ot_parent_discovery_active = false;
        thread_preferred_parent_ot_parent_discovery_unicast = false;
        SetState(kStateIdle);
        mParentCandidate.Clear();
        ExitNow();
    }
"""
    return replace_regex(
        path,
        pattern,
        lambda m: m.group(1) + block,
        already="THREAD_PREFERRED_PARENT_DISCOVERY_ONLY_CANCEL",
        label="mle.cpp discovery-only cancel before Child ID Request",
        dry_run=dry_run,
    )
def patch_mle_parent_response_reporting_call(root: Path, *, dry_run: bool = False) -> str:
    """Patch mle parent response reporting call in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "thread_preferred_parent_ot_notify_parent_response(&parentinfo)" in text:
        return "already"

    call_block = """
    {
        otThreadParentResponseInfo parentinfo;
        parentinfo.mExtAddr = extAddress;
        parentinfo.mRloc16 = sourceAddress;
        parentinfo.mRssi = rss;
        parentinfo.mPriority = connectivityTlv.GetParentPriority();
        parentinfo.mLinkQuality3 = connectivityTlv.GetLinkQuality3();
        parentinfo.mLinkQuality2 = connectivityTlv.GetLinkQuality2();
        parentinfo.mLinkQuality1 = connectivityTlv.GetLinkQuality1();
        parentinfo.mIsAttached = Get<Mle>().IsAttached();
        thread_preferred_parent_ot_notify_parent_response(&parentinfo);
    }
"""

    # Preferred insertion point: after OpenThread's optional parent-response
    # callback block, once source address, ExtAddr, RSSI, and connectivity TLV
    # have all been parsed.
    pattern = (
        r"(#if\s+OPENTHREAD_CONFIG_MLE_PARENT_RESPONSE_CALLBACK_API_ENABLE\s*\n"
        r"\s*if\s*\(\s*mParentResponseCallback\.IsSet\s*\(\s*\)\s*\)\s*\{.*?"
        r"mParentResponseCallback\.Invoke\s*\(\s*&parentinfo\s*\)\s*;\s*\}\s*\n"
        r"#endif\s*)"
    )

    def repl(m):
        """Run repl.

        Returns:
            Result for this helper function.
        """
        return m.group(1).rstrip() + "\n" + call_block

    result = replace_regex(
        path,
        pattern,
        repl,
        already="thread_preferred_parent_ot_notify_parent_response(&parentinfo)",
        label="mle.cpp parent-response reporting call after optional OT callback",
        dry_run=dry_run,
    )
    if result != "missing":
        return result

    # Fallback insertion point for OpenThread revisions without the optional
    # callback block in the source.
    return replace_regex(
        path,
        r"(aRxInfo\.mClass\s*=\s*RxInfo::kAuthoritativeMessage\s*;)",
        lambda m: call_block + "\n    " + m.group(1),
        already="thread_preferred_parent_ot_notify_parent_response(&parentinfo)",
        label="mle.cpp parent-response reporting call before authoritative class",
        dry_run=dry_run,
    )



def patch_mle_parent_response_reporting_is_attached_fix(root: Path, *, dry_run: bool = False) -> str:
    """Fix v5-generated parent response reporting code for Attacher context.

    Inside Mle::Attacher there is no untyped Get(); the OpenThread locator
    requires Get<Mle>() to access the owning MLE object.
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    bad = "parentinfo.mIsAttached = Get().IsAttached();"
    good = "parentinfo.mIsAttached = Get<Mle>().IsAttached();"
    if good in text:
        return "already"
    if bad not in text:
        return "missing"
    return write_if_changed(path, text, text.replace(bad, good), dry_run=dry_run)


def patch_selected_parent_parent_response_filter(root: Path, *, dry_run: bool = False) -> str:
    """During selected-parent attach, ignore Parent Responses from all non-target parents.

    The preflight discovery phase intentionally accepts/logs multicast Parent
    Responses. Once the component starts selected-parent attach, however, the
    attacher must not allow OpenThread's normal parent-selection heuristic to
    choose a different candidate.
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "THREAD_PREFERRED_PARENT_SELECTED_PARENT_RESPONSE_FILTER" in text:
        return "already"

    filter_block = """
    if ((mMode == kSelectedParent) && !(extAddress == mParentCandidate.GetExtAddress()))
    {
        // THREAD_PREFERRED_PARENT_SELECTED_PARENT_RESPONSE_FILTER
        LogNote("SelectedParent ParentResponse ignored non-target src=0x%04x", sourceAddress);
        ExitNow(error = kErrorDrop);
    }
"""

    # Best insertion point: immediately after the component's parent-response
    # reporting call. At this point extAddress, sourceAddress, RSSI, and
    # connectivity TLVs are parsed, but OpenThread has not yet promoted the
    # received response into the active parent candidate.
    result = replace_regex(
        path,
        r"(thread_preferred_parent_ot_notify_parent_response\s*\(\s*&parentinfo\s*\)\s*;\s*\n\s*\}\s*)",
        lambda m: m.group(1).rstrip() + "\n" + filter_block,
        already="THREAD_PREFERRED_PARENT_SELECTED_PARENT_RESPONSE_FILTER",
        label="selected-parent non-target Parent Response filter after reporting call",
        dry_run=dry_run,
    )
    if result != "missing":
        return result

    # Fallback for a source tree where the reporting patch did not land yet.
    return replace_regex(
        path,
        r"(aRxInfo\.mClass\s*=\s*RxInfo::kAuthoritativeMessage\s*;)",
        lambda m: filter_block + "\n    " + m.group(1),
        already="THREAD_PREFERRED_PARENT_SELECTED_PARENT_RESPONSE_FILTER",
        label="selected-parent non-target Parent Response filter before authoritative class",
        dry_run=dry_run,
    )

def patch_parent_response_challenge_log(root: Path, *, dry_run: bool = False) -> str:
    """Patch parent response challenge log in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    old = "    SuccessOrExit(error = aRxInfo.mMessage.ReadAndMatchResponseTlvWith(mParentRequestChallenge));\n"
    new = """    error = aRxInfo.mMessage.ReadAndMatchResponseTlvWith(mParentRequestChallenge);
    if (error != kErrorNone)
    {
        if (mMode == kSelectedParent)
        {
            LogWarn(\"SelectedParent ParentResponse challenge mismatch err=%s keyseq=%lu frame=%lu\",
                    ErrorToString(error), ToUlong(aRxInfo.mKeySequence), ToUlong(aRxInfo.mFrameCounter));
        }
        ExitNow();
    }
"""
    return replace_literal(path, old, new, already="SelectedParent ParentResponse challenge mismatch", dry_run=dry_run)


def patch_parent_response_rx_log(root: Path, *, dry_run: bool = False) -> str:
    """Patch parent response rx log in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    old = """    aRxInfo.mClass = RxInfo::kAuthoritativeMessage;

#if OPENTHREAD_FTD
"""
    new = """    aRxInfo.mClass = RxInfo::kAuthoritativeMessage;
    if (mMode == kSelectedParent)
    {
        LogNote(\"SelectedParent ParentResponse rx src=0x%04x rss=%d\", sourceAddress, rss);
    }

#if OPENTHREAD_FTD
"""
    return replace_literal(path, old, new, already="SelectedParent ParentResponse rx", dry_run=dry_run)


def patch_child_id_request_sent_log(root: Path, *, dry_run: bool = False) -> str:
    """Patch child id request sent log in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    old = """        if (HasAcceptableParentCandidate() && (SendChildIdRequest() == kErrorNone))
        {
            SetState(kStateChildIdRequest);
"""
    new = """        if (HasAcceptableParentCandidate() && (SendChildIdRequest() == kErrorNone))
        {
            if (mMode == kSelectedParent)
            {
                LogNote(\"SelectedParent ChildIdRequest sent cand=0x%04x timeout=%lu\",
                        mParentCandidate.GetRloc16(), ToUlong(kChildIdResponseTimeout));
            }
            SetState(kStateChildIdRequest);
"""
    if old not in normalize_newlines(path.read_text()):
        # ESP-IDF 5.5.x may include jitter and decrement after SetState; this regex targets the common prefix only.
        return replace_regex(
            path,
            r"(if\s*\(\s*HasAcceptableParentCandidate\s*\(\s*\)\s*&&\s*\(\s*SendChildIdRequest\s*\(\s*\)\s*==\s*kErrorNone\s*\)\s*\)\s*\{\s*)(SetState\s*\(\s*kStateChildIdRequest\s*\)\s*;)",
            lambda m: m.group(1) + "\n            if (mMode == kSelectedParent)\n            {\n                LogNote(\"SelectedParent ChildIdRequest sent cand=0x%04x timeout=%lu\",\n                        mParentCandidate.GetRloc16(), ToUlong(kChildIdResponseTimeout));\n            }\n            " + m.group(2),
            already="SelectedParent ChildIdRequest sent",
            label="selected-parent ChildIdRequest sent log",
            dry_run=dry_run,
        )
    return replace_literal(path, old, new, already="SelectedParent ChildIdRequest sent", dry_run=dry_run)


def patch_child_id_request_send_fail_log(root: Path, *, dry_run: bool = False) -> str:
    """Patch child id request send fail log in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    old = """exit:
    FreeMessageOnError(message, error);
    return error;
}
"""
    new = """exit:
    if ((error != kErrorNone) && (mMode == kSelectedParent))
    {
        LogWarn(\"SelectedParent ChildIdRequest send failed err=%s cand=0x%04x\",
                ErrorToString(error), mParentCandidate.GetRloc16());
    }
    FreeMessageOnError(message, error);
    return error;
}
"""
    # This exact exit block exists in multiple functions. Restrict to the SendChildIdRequest function.
    text = normalize_newlines(path.read_text())
    if "SelectedParent ChildIdRequest send failed" in text:
        return "already"
    pattern = r"(Error\s+Mle::Attacher::SendChildIdRequest\s*\([^)]*\)\s*\{.*?)(exit:\s*\n\s*FreeMessageOnError\s*\(\s*message\s*,\s*error\s*\)\s*;\s*\n\s*return\s+error\s*;\s*\n\s*\})"
    def repl(m):
        """Run repl.

        Returns:
            Result for this helper function.
        """
        block = "exit:\n    if ((error != kErrorNone) && (mMode == kSelectedParent))\n    {\n        LogWarn(\"SelectedParent ChildIdRequest send failed err=%s cand=0x%04x\",\n                ErrorToString(error), mParentCandidate.GetRloc16());\n    }\n    FreeMessageOnError(message, error);\n    return error;\n}"
        return m.group(1) + block
    return replace_regex(path, pattern, repl, already="SelectedParent ChildIdRequest send failed", label="selected-parent ChildIdRequest send failure log", dry_run=dry_run)


def patch_child_id_request_timeout_log(root: Path, *, dry_run: bool = False) -> str:
    """Patch child id request timeout log in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    old = """    case kStateChildIdRequest:
        SetState(kStateIdle);
        mParentCandidate.Clear();
        delay = Reattach();
        break;
"""
    new = """    case kStateChildIdRequest:
        if (mMode == kSelectedParent)
        {
            LogWarn(\"SelectedParent ChildIdRequest timed out cand=0x%04x\", mParentCandidate.GetRloc16());
        }
        SetState(kStateIdle);
        mParentCandidate.Clear();
        delay = Reattach();
        break;
"""
    return replace_literal(path, old, new, already="SelectedParent ChildIdRequest timed out", dry_run=dry_run)


def patch_child_id_response_security_log(root: Path, *, dry_run: bool = False) -> str:
    """Patch child id response security log in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    old = """    VerifyOrExit(aRxInfo.IsNeighborStateValid(), error = kErrorSecurity);
    VerifyOrExit(mState == kStateChildIdRequest);
"""
    new = """    if ((mMode == kSelectedParent) && !aRxInfo.IsNeighborStateValid())
    {
        LogWarn(\"SelectedParent ChildIdResponse neighbor invalid src=0x%04x keyseq=%lu frame=%lu\",
                sourceAddress, ToUlong(aRxInfo.mKeySequence), ToUlong(aRxInfo.mFrameCounter));
        ExitNow(error = kErrorSecurity);
    }
    VerifyOrExit(aRxInfo.IsNeighborStateValid(), error = kErrorSecurity);
    VerifyOrExit(mState == kStateChildIdRequest);
"""
    return replace_literal(path, old, new, already="SelectedParent ChildIdResponse neighbor invalid", dry_run=dry_run)


def patch_child_id_response_accept_log(root: Path, *, dry_run: bool = False) -> str:
    """Patch child id response accept log in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    old = """    Get().SetStateChild(shortAddress);
"""
    new = """    Get().SetStateChild(shortAddress);
    if (mMode == kSelectedParent)
    {
        LogNote(\"SelectedParent ChildIdResponse accepted parent=0x%04x child=0x%04x\", sourceAddress, shortAddress);
    }
"""
    return replace_literal(path, old, new, already="SelectedParent ChildIdResponse accepted", dry_run=dry_run)


def patch_child_id_response_reject_log(root: Path, *, dry_run: bool = False) -> str:
    """Patch child id response reject log in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "SelectedParent ChildIdResponse reject" in text:
        return "already"
    pattern = r"(void\s+Mle::Attacher::HandleChildIdResponse\s*\([^)]*\)\s*\{.*?)(exit:\s*\n\s*LogProcessError\s*\(\s*kTypeChildIdResponse\s*,\s*error\s*\)\s*;\s*\n\s*\})"
    def repl(m):
        """Run repl.

        Returns:
            Result for this helper function.
        """
        block = "exit:\n    if ((error != kErrorNone) && (mMode == kSelectedParent))\n    {\n        LogWarn(\"SelectedParent ChildIdResponse reject err=%s src=0x%04x short=0x%04x\",\n                ErrorToString(error), sourceAddress, shortAddress);\n    }\n    LogProcessError(kTypeChildIdResponse, error);\n}"
        return m.group(1) + block
    return replace_regex(path, pattern, repl, already="SelectedParent ChildIdResponse reject", label="selected-parent ChildIdResponse reject log", dry_run=dry_run)


def patch_parent_response_reject_log(root: Path, *, dry_run: bool = False) -> str:
    """Patch parent response reject log in the OpenThread sources.

    Args:
        root: OpenThread src/core root directory.
        dry_run: Whether to report changes without writing files.

    Returns:
        Patch state string such as "already", "missing", or "patched".
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "SelectedParent ParentResponse reject" in text:
        return "already"
    pattern = r"(void\s+Mle::Attacher::HandleParentResponse\s*\([^)]*\)\s*\{.*?)(exit:\s*\n\s*LogProcessError\s*\(\s*kTypeParentResponse\s*,\s*error\s*\)\s*;\s*\n\s*\})"
    def repl(m):
        """Run repl.

        Returns:
            Result for this helper function.
        """
        block = "exit:\n    if ((error != kErrorNone) && (mMode == kSelectedParent))\n    {\n        LogWarn(\"SelectedParent ParentResponse reject err=%s src=0x%04x rss=%d cand=0x%04x\",\n                ErrorToString(error), sourceAddress, rss, mParentCandidate.GetRloc16());\n    }\n    LogProcessError(kTypeParentResponse, error);\n}"
        return m.group(1) + block
    return replace_regex(path, pattern, repl, already="SelectedParent ParentResponse reject", label="selected-parent ParentResponse reject log", dry_run=dry_run)



def patch_attach_method_preseed_candidate(root: Path, *, dry_run: bool = False) -> str:
    """Pre-seed the selected parent candidate before Attach(kSelectedParent).

    Some OpenThread revisions synchronously construct/send the Parent Request
    during Attach(kSelectedParent). The original hook set the ExtAddr only after
    Attach(), which is safe for asynchronous revisions but can be too late for
    synchronous ones. We set it before and after Attach(); the second assignment
    preserves the value if Attach() clears the candidate during initialization.
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "THREAD_PREFERRED_PARENT_PRESEED_SELECTED_PARENT" in text:
        return "already"

    pattern = (
        r"(Error\s+Mle::AttachToSelectedParent\s*\(\s*const\s+Mac::ExtAddress\s*&\s*aExtAddress\s*\)\s*\{.*?"
        r"VerifyOrExit\s*\(\s*!IsAttaching\s*\(\s*\)\s*,\s*error\s*=\s*kErrorBusy\s*\)\s*;\s*)"
        r"(?:\s*//\s*THREAD_PREFERRED_PARENT_FORCE_DETACH_BEFORE_ATTACH\n(?:\s*//[^\n]*\n)*\s*\(void\)BecomeDetached\s*\(\s*\)\s*;\s*)?"
        r"(mAttacher\.Attach\s*\(\s*kSelectedParent\s*\)\s*;\s*)"
        r"(mAttacher\.GetParentCandidate\s*\(\s*\)\.SetExtAddress\s*\(\s*aExtAddress\s*\)\s*;)"
    )

    def repl(m):
        """Run repl.

        Returns:
            Result for this helper function.
        """
        return (
            m.group(1)
            + "\n    // THREAD_PREFERRED_PARENT_PRESEED_SELECTED_PARENT\n"
            + "    // Make the target ExtAddr visible before Attach(kSelectedParent)\n"
            + "    // in case this OpenThread revision sends the Parent Request\n"
            + "    // synchronously from Attach(). Repeat after Attach() because\n"
            + "    // Attach() may clear/reinitialize the parent candidate.\n"
            + "    mAttacher.GetParentCandidate().SetExtAddress(aExtAddress);\n\n    "
            + m.group(2)
            + "    "
            + m.group(3)
        )

    return replace_regex(
        path,
        pattern,
        repl,
        already="THREAD_PREFERRED_PARENT_PRESEED_SELECTED_PARENT",
        label="selected-parent candidate preseed before Attach",
        dry_run=dry_run,
    )


def patch_remove_force_detach_before_attach(root: Path, *, dry_run: bool = False) -> str:
    """Remove the older force-detach block from v10 builds.

    The biparental targeted attach path keeps the child attached while the
    selected-parent Child ID exchange is attempted. Forcing BecomeDetached()
    first can race the targeted attach against OpenThread's generic reattach
    path and frequently returns to the old, stronger parent before the selected
    parent completes.
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    marker = "THREAD_PREFERRED_PARENT_FORCE_DETACH_BEFORE_ATTACH"
    if marker not in text:
        return "already"

    pattern = (
        r"\n\s*//\s*THREAD_PREFERRED_PARENT_FORCE_DETACH_BEFORE_ATTACH\n"
        r"(?:\s*//[^\n]*\n)*"
        r"\s*\(void\)BecomeDetached\s*\(\s*\)\s*;\n+"
    )
    new, count = re.subn(pattern, "\n", text, count=1, flags=re.MULTILINE)
    if count == 0:
        print("[thread_preferred_parent][detail] no regex match for old force-detach block")
        return "missing"
    return write_if_changed(path, text, new, dry_run=dry_run)


def patch_selected_parent_child_id_request_bypass(root: Path, *, dry_run: bool = False) -> str:
    """For selected-parent mode, send Child ID Request once the target responded.

    The observed failure mode is: the target Parent Response is seen, selected
    attach starts, but no selected-parent Child ID Request/Response completion is
    observed. The generic HasAcceptableParentCandidate() path can still reject a
    requested parent because it is not "better" than the current one. For the
    explicit selected-parent path, a matching Parent Response is enough to try
    Child ID Request; success is still verified by the final attached parent.
    """
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "THREAD_PREFERRED_PARENT_SELECTED_PARENT_FORCE_CHILD_ID" in text:
        return "already"

    block = """
        if ((mMode == kSelectedParent) && mParentCandidate.IsStateParentResponse())
        {
            // THREAD_PREFERRED_PARENT_SELECTED_PARENT_FORCE_CHILD_ID
            // In selected-parent mode the target has already been constrained
            // by ExtAddr and a Parent Response has populated mParentCandidate.
            // Do not let generic \"better parent\" heuristics veto the explicit
            // user-requested parent merely because the old parent has stronger
            // RSSI/link metrics.
            Error childIdError = SendChildIdRequest();
            if (childIdError == kErrorNone)
            {
                LogNote(\"SelectedParent ChildIdRequest forced cand=0x%04x timeout=%lu\",
                        mParentCandidate.GetRloc16(), ToUlong(kChildIdResponseTimeout));
                SetState(kStateChildIdRequest);
                delay = kChildIdResponseTimeout;
                ExitNow();
            }

            LogWarn(\"SelectedParent ChildIdRequest forced send failed err=%s cand=0x%04x\",
                    ErrorToString(childIdError), mParentCandidate.GetRloc16());
        }

"""

    pattern = (
        r"(\n\s*if\s*\(\s*HasAcceptableParentCandidate\s*\(\s*\)\s*&&\s*"
        r"\(\s*SendChildIdRequest\s*\(\s*\)\s*==\s*kErrorNone\s*\)\s*\)\s*\{)"
    )
    return replace_regex(
        path,
        pattern,
        lambda m: "\n" + block.rstrip() + m.group(1),
        already="THREAD_PREFERRED_PARENT_SELECTED_PARENT_FORCE_CHILD_ID",
        label="selected-parent force Child ID Request after target Parent Response",
        dry_run=dry_run,
    )


def patch_child_id_response_security_log_regex(root: Path, *, dry_run: bool = False) -> str:
    """Fallback diagnostic patch for ESP-IDF/OpenThread revisions with changed formatting."""
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "SelectedParent ChildIdResponse neighbor invalid" in text:
        return "already"

    pattern = (
        r"(VerifyOrExit\s*\(\s*aRxInfo\.IsNeighborStateValid\s*\(\s*\)\s*,\s*error\s*=\s*kErrorSecurity\s*\)\s*;\s*\n\s*"
        r"VerifyOrExit\s*\(\s*mState\s*==\s*kStateChildIdRequest\s*\)\s*;)"
    )
    block = """if ((mMode == kSelectedParent) && !aRxInfo.IsNeighborStateValid())
    {
        LogWarn(\"SelectedParent ChildIdResponse neighbor invalid src=0x%04x keyseq=%lu frame=%lu\",
                sourceAddress, ToUlong(aRxInfo.mKeySequence), ToUlong(aRxInfo.mFrameCounter));
        ExitNow(error = kErrorSecurity);
    }
    """
    return replace_regex(
        path,
        pattern,
        lambda m: block + m.group(1),
        already="SelectedParent ChildIdResponse neighbor invalid",
        label="selected-parent ChildIdResponse security diagnostics regex",
        dry_run=dry_run,
    )


def patch_child_id_response_accept_log_regex(root: Path, *, dry_run: bool = False) -> str:
    """Fallback accepted-log patch for both Get().SetStateChild and Get<Mle>().SetStateChild."""
    path = root / "thread/mle.cpp"
    text = normalize_newlines(path.read_text())
    if "SelectedParent ChildIdResponse accepted" in text:
        return "already"

    pattern = r"((?:Get\s*(?:<\s*Mle\s*>)?\s*\(\s*\)\s*\.\s*)?SetStateChild\s*\(\s*shortAddress\s*\)\s*;)"
    block = """
    if (mMode == kSelectedParent)
    {
        LogNote(\"SelectedParent ChildIdResponse accepted parent=0x%04x child=0x%04x\", sourceAddress, shortAddress);
    }"""
    return replace_regex(
        path,
        pattern,
        lambda m: m.group(1) + block,
        already="SelectedParent ChildIdResponse accepted",
        label="selected-parent ChildIdResponse accepted diagnostics regex",
        dry_run=dry_run,
    )

def apply_patches(root: Path, *, dry_run: bool = False) -> int:
    """Apply the full preferred-parent patch set to an OpenThread src/core tree."""
    root = root.expanduser().resolve()
    print(f"[thread_preferred_parent] OpenThread src/core: {root}")

    if not root.exists():
        print(f"[thread_preferred_parent][missing-root] {root}")
        return 1

    restore_truncated_mle_cpp_if_needed(root, dry_run=dry_run)

    patches = [
        ("mle.hpp declaration", root / "thread/mle.hpp", patch_mle_hpp, True),
        ("mle.cpp AttachToSelectedParent", root / "thread/mle.cpp", patch_attach_method, True),
        ("mle.cpp remove old force-detach selected-parent block", root / "thread/mle.cpp", patch_remove_force_detach_before_attach, True),
        ("mle.cpp selected-parent candidate preseed", root / "thread/mle.cpp", patch_attach_method_preseed_candidate, True),
        ("mle.cpp selected-router destination", root / "thread/mle.cpp", patch_selected_parent_destination, True),
        ("mle.cpp selected-parent bypass", root / "thread/mle.cpp", patch_accept_selected_parent_without_current_parent_response, True),
        ("mle.cpp selected-parent force Child ID Request", root / "thread/mle.cpp", patch_selected_parent_child_id_request_bypass, True),
        ("thread_api.cpp bridge", root / "api/thread_api.cpp", patch_thread_api, True),
        ("thread_api.cpp parent-response reporting bridge", root / "api/thread_api.cpp", patch_thread_api_parent_response_reporting, True),
        ("mle.cpp parent-response reporting declaration", root / "thread/mle.cpp", patch_mle_parent_response_reporting_declaration, True),
        ("thread_api.cpp discovery-only bridge", root / "api/thread_api.cpp", patch_thread_api_discovery_bridge, True),
        ("mle.cpp discovery-only declaration", root / "thread/mle.cpp", patch_mle_discovery_declaration, True),
        ("mle.cpp selected-parent interrupt discovery", root / "thread/mle.cpp", patch_attach_method_interrupt_discovery, True),
        ("mle.cpp discovery-only cancel", root / "thread/mle.cpp", patch_mle_discovery_cancel, True),
        ("mle.cpp parent-response reporting call", root / "thread/mle.cpp", patch_mle_parent_response_reporting_call, True),
        ("mle.cpp parent-response reporting IsAttached fix", root / "thread/mle.cpp", patch_mle_parent_response_reporting_is_attached_fix, True),
        ("mle.cpp selected-parent non-target Parent Response filter", root / "thread/mle.cpp", patch_selected_parent_parent_response_filter, True),
        ("diag ParentResponse challenge", root / "thread/mle.cpp", patch_parent_response_challenge_log, False),
        ("diag ParentResponse rx", root / "thread/mle.cpp", patch_parent_response_rx_log, False),
        ("diag ParentResponse reject", root / "thread/mle.cpp", patch_parent_response_reject_log, False),
        ("diag ChildIdRequest sent", root / "thread/mle.cpp", patch_child_id_request_sent_log, False),
        ("diag ChildIdRequest send fail", root / "thread/mle.cpp", patch_child_id_request_send_fail_log, False),
        ("diag ChildIdRequest timeout", root / "thread/mle.cpp", patch_child_id_request_timeout_log, False),
        ("diag ChildIdResponse security", root / "thread/mle.cpp", patch_child_id_response_security_log, False),
        ("diag ChildIdResponse security regex fallback", root / "thread/mle.cpp", patch_child_id_response_security_log_regex, False),
        ("diag ChildIdResponse reject", root / "thread/mle.cpp", patch_child_id_response_reject_log, False),
        ("diag ChildIdResponse accepted", root / "thread/mle.cpp", patch_child_id_response_accept_log, False),
        ("diag ChildIdResponse accepted regex fallback", root / "thread/mle.cpp", patch_child_id_response_accept_log_regex, False),
    ]

    rc = 0
    optional_missing = 0
    for label, path, func, required in patches:
        if not path.exists():
            print(f"[thread_preferred_parent][missing-file] {path}")
            if required:
                rc = 1
            else:
                optional_missing += 1
            continue
        state = func(root, dry_run=dry_run)
        print(f"[thread_preferred_parent][{state}] {label}: {path}")
        if state == "missing":
            if required:
                rc = 1
            else:
                optional_missing += 1

    if rc == 0:
        print("[thread_preferred_parent] OpenThread selected-parent hook is installed.")
        if optional_missing:
            print(f"[thread_preferred_parent] {optional_missing} optional diagnostic patch(es) did not match this OpenThread revision.")
    else:
        print("[thread_preferred_parent] Required patch did not match this OpenThread revision.")
    return rc


def platformio_package_root() -> Optional[Path]:
    """Return PlatformIO's package dir when running as an extra_script."""
    try:
        Import("env")  # type: ignore[name-defined]  # noqa: F821
    except Exception:
        return None

    packages_dir = env.subst("$PROJECT_PACKAGES_DIR")  # type: ignore[name-defined]  # noqa: F821
    if not packages_dir or "$" in packages_dir:
        packages_dir = os.environ.get("PLATFORMIO_PACKAGES_DIR", "")
    if not packages_dir:
        return None
    return Path(packages_dir)


def run_as_platformio_script() -> Optional[int]:
    """Patch the active PlatformIO framework package when imported as a hook."""
    packages_dir = platformio_package_root()
    if packages_dir is None:
        return None

    root = packages_dir / FRAMEWORK_RELATIVE_CORE
    rc = apply_patches(root)
    if rc != 0:
        raise SystemExit(rc)
    return rc


def main() -> int:
    """CLI entry point for manually patching a chosen OpenThread source tree."""
    parser = argparse.ArgumentParser(description="Patch ESP-IDF's vendored OpenThread core.")
    parser.add_argument("root", nargs="?", type=Path, default=DEFAULT_ROOT, help="OpenThread src/core root to patch")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be patched without writing files")
    args = parser.parse_args()
    return apply_patches(args.root, dry_run=args.dry_run)


_pio_rc = run_as_platformio_script()

if __name__ == "__main__":
    if _pio_rc is not None:
        raise SystemExit(_pio_rc)
    raise SystemExit(main())
