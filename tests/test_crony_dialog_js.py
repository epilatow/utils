#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15,<3.16"
# dependencies = ["pytest"]
# ///
# This is AI generated code

"""Behavior tests for the JXA dialog crony draws on macOS.

`src/crony/platform/dialog.js` is the window both desktop prompts appear
in, and its reason for existing is that appearing must not take the
keyboard from the application the user is working in. That is a runtime
property -- it lives in what AppKit does with a window shown without
being made key -- so a mocked subprocess cannot show it. These tests run the
real script under osascript and assert on the window it actually built.

Every test that builds a dialog puts a window on screen and an icon in
the Dock, so those are behind `--run-visible-dialog` and a default run
does none of it. That is why they do not work around being seen: an
opt-in run is one the caller asked for, so the dialogs are presented
exactly as production presents them. `TestDialogUsage` is the exception
and runs by default -- `run` rejects a short argv before it reaches
`buildDialog`, so nothing is drawn and no activation policy is set.

Opted in on a host with no reachable window server, the harness fails
rather than skipping -- a suite somebody explicitly asked for that
quietly passed would hide the missing coverage.

What no offline test can reach is whether a *human* click is delivered,
or whether focus is taken; both need the window server to route a real
event. The manual checks in DEVELOPMENT.md cover those.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, TypedDict

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from crony.platform import darwin

REPO_ROOT = Path(__file__).parent.parent
# The subject is a JavaScript file, so there is no module to measure
# coverage against; name this file, as the other asset suites do.
_script_path = Path(__file__)
DIALOG_JS = REPO_ROOT / "src" / "crony" / "platform" / "dialog.js"


# All AppKit behavior tests are opt-in, including windowless dispatch tests.
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="the dialog is Cocoa"
)

# Drives the shipped dialog.js and reports what it built. Every check
# runs in this one script so the suite pays a single osascript launch
# and shows its windows once. Character codes carry the key equivalents
# so an empty one is unambiguous, and String.fromCharCode keeps the
# harness free of backslash escapes.
_HARNESS = """
ObjC.import('Foundation');

eval(ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(
    '%DIALOG_JS%', $.NSUTF8StringEncoding, null)));

function codes(value) {
    const text = ObjC.unwrap(value);
    const out = [];
    for (let i = 0; i < text.length; i++) {
        out.push(text.charCodeAt(i));
    }
    return out;
}

function pump(seconds) {
    $.NSRunLoop.currentRunLoop.runUntilDate(
        $.NSDate.dateWithTimeIntervalSinceNow(seconds));
}

function main() {
    const result = {};

    const approval = buildDialog({
        title: 'crony: synthetic job',
        message: 'crony wants to run this. Now?',
        buttons: ['Cancel Job', 'Delay Job', 'Run Job'],
        caution: false,
    });
    // Before presenting, the default button holds the Return
    // equivalent this file asks for; AppKit takes it away below.
    result.defaultKeyBeforePresent = codes(
        approval.buttons[2].keyEquivalent);
    presentDialog(approval);
    pump(0.3);
    result.appActive = $.NSApplication.sharedApplication.isActive;
    result.windowIsKey = approval.window.isKeyWindow;
    result.windowVisible = approval.window.isVisible;
    // The structure that avoids taking the keyboard on presentation.
    result.isPanel = approval.window.isKindOfClass($.NSPanel);
    result.styleMask = Number(approval.window.styleMask);
    result.becomesKeyOnlyIfNeeded = approval.window.becomesKeyOnlyIfNeeded;
    result.collectionBehavior = Number(approval.window.collectionBehavior);
    // Number(): an ObjC-bridged property stringifies as itself, so the
    // JSON would otherwise carry '3' rather than 3.
    result.windowLevel = Number(approval.window.level);
    result.buttonTitles = approval.buttons.map(function (button) {
        return ObjC.unwrap(button.title);
    });
    result.defaultKeyCodes = codes(approval.buttons[2].keyEquivalent);
    result.cancelKeyCodes = codes(approval.buttons[0].keyEquivalent);
    result.plainLabelLeft = approval.geometry.labelFrame.origin.x;
    result.shortHeight = approval.geometry.contentHeight;
    result.answeredBeforeClick = isAnswered();

    // A click driven through the real target/action wiring.
    approval.buttons[2].performClick(null);
    pump(0.3);
    result.clicked = answer();
    result.answeredAfterClick = isAnswered();
    approval.window.close;

    const lines = [];
    for (let i = 0; i < 14; i++) {
        lines.push('line ' + i + ' of a run summary');
    }
    const failure = buildDialog({
        title: 'crony: synthetic job failed',
        message: lines.join(String.fromCharCode(10)),
        buttons: ['OK'],
        caution: true,
    });
    pump(0.2);
    result.tallHeight = failure.geometry.contentHeight;
    result.cautionLabelLeft = failure.geometry.labelFrame.origin.x;
    result.failureButtons = failure.buttons.map(function (button) {
        return ObjC.unwrap(button.title);
    });
    failure.window.close;

    // A window dismissed without a click answers nothing.
    const dismissed = buildDialog({
        title: 'crony: synthetic job',
        message: 'closed without a click',
        buttons: ['Cancel Job', 'Run Job'],
        caution: false,
    });
    pump(0.2);
    dismissed.window.close;
    pump(0.3);
    result.dismissedAnswered = isAnswered();
    result.dismissedAnswer = answer();

    return JSON.stringify(result);
}

// dialog.js defines run(argv), which osascript would otherwise call
// with this harness's own empty argv.
run = main;
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory: Any) -> dict[str, Any]:
    """What the real dialog.js built, from one osascript run."""
    harness = tmp_path_factory.mktemp("dialog") / "harness.js"
    harness.write_text(_HARNESS.replace("%DIALOG_JS%", str(DIALOG_JS)))
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", str(harness)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    parsed: dict[str, Any] = json.loads(proc.stdout)
    return parsed


@pytest.mark.visible_dialog
class TestDialogTakesNoFocus:
    """The mechanism that stops the dialog taking the keyboard.

    Read what these do and do not establish. Focus theft itself is not
    observable from here: `NSApp.isActive` reports what the app has
    processed rather than what the window server did, and it read
    `false` on a build that was demonstrably swallowing keystrokes. So
    the assertions below pin the *structure* that avoids taking the
    keyboard on presentation -- a non-activating panel -- and
    treat the live readings as corroboration only. The gate for the
    property is the manual typing check in DEVELOPMENT.md.
    """

    def test_window_is_a_nonactivating_panel(
        self, built: dict[str, Any]
    ) -> None:
        # The load-bearing fact. A plain NSWindow can become key, and
        # declining to activate does not stop the window server routing
        # keystrokes to it; NSWindowStyleMaskNonactivatingPanel does.
        assert built["isPanel"] is True
        assert built["styleMask"] & 128 == 128

    def test_panel_takes_key_status_only_if_needed(
        self, built: dict[str, Any]
    ) -> None:
        # Buttons do not pull key status; selecting the message can.
        assert built["becomesKeyOnlyIfNeeded"] is True

    def test_panel_joins_the_frontmost_space(
        self, built: dict[str, Any]
    ) -> None:
        # CanJoinAllSpaces | FullScreenAuxiliary. Without these the
        # panel stays on the desktop Space, so it would be invisible
        # over a full-screen app -- which is exactly the situation the
        # approval prompt fires in, since it waits for continuous typing.
        assert built["collectionBehavior"] & 1 == 1
        assert built["collectionBehavior"] & 256 == 256

    def test_app_is_not_active_while_the_dialog_is_up(
        self, built: dict[str, Any]
    ) -> None:
        # Corroboration, not a guarantee: a build that stole the
        # keyboard also reported false here. It still earns its place --
        # a change that made the app activate outright would trip it.
        assert built["appActive"] is False

    def test_window_is_shown_but_not_key(self, built: dict[str, Any]) -> None:
        # Visible, so the user can see and click it; not key, so it
        # receives none of their typing.
        assert built["windowVisible"] is True
        assert built["windowIsKey"] is False

    def test_default_button_has_no_return_equivalent(
        self, built: dict[str, Any]
    ) -> None:
        # AppKit disables the default button's Return equivalent while
        # its window is not key. A stray Return cannot run the job before
        # the user deliberately focuses the message.
        # Asserting the binding was set before presenting keeps this
        # honest: an empty result either way would also pass on a file
        # that never asked for the binding at all.
        assert built["defaultKeyBeforePresent"] == [13]
        assert built["defaultKeyCodes"] == []

    def test_window_floats(self, built: dict[str, Any]) -> None:
        # NSFloatingWindowLevel: what replaces activation as the way a
        # dialog nobody focused still gets noticed.
        assert built["windowLevel"] == 3


@pytest.mark.visible_dialog
class TestDialogChoice:
    """Buttons carry the HostPlatform first..last contract and report
    the clicked label."""

    def test_buttons_are_built_in_order(self, built: dict[str, Any]) -> None:
        assert built["buttonTitles"] == [
            "Cancel Job",
            "Delay Job",
            "Run Job",
        ]

    def test_cancel_button_takes_escape(self, built: dict[str, Any]) -> None:
        # 27 is ESC. Unlike the default button's Return, AppKit leaves
        # this one set; it is still unreachable until the window is key.
        assert built["cancelKeyCodes"] == [27]

    def test_click_reports_its_label(self, built: dict[str, Any]) -> None:
        assert built["answeredBeforeClick"] is False
        assert built["answeredAfterClick"] is True
        assert built["clicked"] == "Run Job"

    def test_dismissed_window_answers_nothing(
        self, built: dict[str, Any]
    ) -> None:
        # Closing ends the wait -- crony reads the empty answer as a
        # cancel -- rather than leaving the dialog blocking forever.
        assert built["dismissedAnswered"] is True
        assert built["dismissedAnswer"] == ""


_COPY_HARNESS = """
ObjC.import('Foundation');
eval(ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(
    '%DIALOG_JS%', $.NSUTF8StringEncoding, null)));

function main() {
    const app = $.NSApplication.sharedApplication;
    const retained = $.NSMutableArray.array;
    let actions = [];
    ObjC.registerSubclass({
        name: 'CronyShortcutTestEditor',
        superclass: 'NSObject',
        methods: {
            'copy:': {
                types: ['void', ['id']],
                implementation: function () {
                    actions.push('copy');
                },
            },
            'selectAll:': {
                types: ['void', ['id']],
                implementation: function () {
                    actions.push('selectAll');
                },
            },
        },
    });
    const command = 1 << 20;
    const cases = [
        {name: 'copy', pump: true},
        {name: 'selectAllThenCopy', pump: true, keys: ['a', 'c']},
        {name: 'capsLockCopy', pump: true, keys: ['C'],
            flags: command | (1 << 16)},
        {name: 'notKey', keyWindow: false},
        {name: 'noEditor', editor: false},
        {name: 'otherResponder', responder: false},
        {name: 'withoutCommand', flags: 0},
        {name: 'shift', flags: command | (1 << 17)},
        {name: 'control', flags: command | (1 << 18)},
        {name: 'option', flags: command | (1 << 19)},
        {name: 'otherKey', keys: ['x']},
        {name: 'keyUp', type: 11},
    ];
    const result = {};
    cases.forEach(function (test) {
        actions = [];
        const editor = $.CronyShortcutTestEditor.alloc.init;
        retained.addObject(editor);
        const keys = test.keys || ['c'];
        const responder = test.responder === false
            ? $.NSObject.alloc.init : editor;
        retained.addObject(responder);
        // Focus is a controlled input. Events and target/action dispatch
        // use AppKit, without asking the window server for focus.
        const dialog = {
            window: {
                isKeyWindow: test.keyWindow !== false,
                firstResponder: responder,
                get isVisible() { return actions.length < keys.length; },
            },
            messageField: {
                currentEditor: test.editor === false ? $() : editor,
            },
        };
        const events = keys.map(function (key) {
            return $.NSEvent
                .keyEventWithTypeLocationModifierFlagsTimestampWindowNumberContextCharactersCharactersIgnoringModifiersIsARepeatKeyCode(
                    test.type || 10, $.NSMakePoint(0, 0),
                    test.flags === undefined ? command : test.flags,
                    0, 0, $(), key, key, false, key === 'a' ? 0 : 8);
        });
        let handled = null;
        if (test.pump) {
            for (let i = events.length - 1; i >= 0; i--) {
                app.postEventAtStart(events[i], true);
            }
            pumpUntilAnswered(dialog);
        } else {
            handled = handleMessageShortcut(dialog, events[0]);
        }
        result[test.name] = {
            actions: actions,
            handled: handled,
        };
    });
    return JSON.stringify(result);
}
run = main;
"""


class _ShortcutResult(TypedDict):
    actions: list[str]
    handled: bool | None


@pytest.fixture(scope="module")
def shortcuts() -> dict[str, _ShortcutResult]:
    proc = subprocess.run(
        [
            "osascript",
            "-l",
            "JavaScript",
            "-e",
            _COPY_HARNESS.replace("%DIALOG_JS%", str(DIALOG_JS)),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    parsed: dict[str, _ShortcutResult] = json.loads(proc.stdout)
    return parsed


@pytest.mark.visible_dialog
class TestDialogCopy:
    @pytest.mark.parametrize(
        ("case", "expected"),
        [
            ("copy", ["copy"]),
            ("selectAllThenCopy", ["selectAll", "copy"]),
            ("capsLockCopy", ["copy"]),
        ],
    )
    def test_pump_dispatches_shortcuts(
        self,
        shortcuts: dict[str, _ShortcutResult],
        case: str,
        expected: list[str],
    ) -> None:
        assert shortcuts[case]["actions"] == expected

    @pytest.mark.parametrize(
        "case",
        [
            "notKey",
            "noEditor",
            "otherResponder",
            "withoutCommand",
            "shift",
            "control",
            "option",
            "otherKey",
            "keyUp",
        ],
    )
    def test_other_events_are_not_consumed(
        self, shortcuts: dict[str, _ShortcutResult], case: str
    ) -> None:
        assert shortcuts[case]["handled"] is False
        assert shortcuts[case]["actions"] == []


@pytest.mark.visible_dialog
class TestDialogLayout:
    """The window is sized from its content, so neither prompt is
    clipped."""

    def test_failure_body_grows_the_window(self, built: dict[str, Any]) -> None:
        # A 14-line run summary must not be clipped to the height of a
        # one-line approval prompt.
        assert built["tallHeight"] > built["shortHeight"]

    def test_caution_icon_insets_the_label(self, built: dict[str, Any]) -> None:
        assert built["cautionLabelLeft"] > built["plainLabelLeft"]

    def test_failure_dialog_has_one_button(self, built: dict[str, Any]) -> None:
        assert built["failureButtons"] == ["OK"]


# Drives dialog.js's own `run(argv)` -- the entry point production uses,
# and the one thing the mocked Python tests cannot reach, since they
# assert only the argv crony emits. `buildDialog` is wrapped to report
# what `run` parsed, and `pumpUntilAnswered` is replaced so the dialog is
# answered without a user; the presentation itself is left alone. What
# `run` parsed goes to stderr, so stdout stays exactly what a real
# invocation prints -- which is what `show_dialog` parses.
_ARGV_HARNESS = """
ObjC.import('Foundation');

eval(ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(
    '%DIALOG_JS%', $.NSUTF8StringEncoding, null)));

const realBuild = buildDialog;
buildDialog = function (opts) {
    $.NSFileHandle.fileHandleWithStandardError.writeData(
        $.NSString.alloc.initWithUTF8String(JSON.stringify({
            title: opts.title,
            message: opts.message,
            buttons: opts.buttons,
            caution: opts.caution,
        })).dataUsingEncoding($.NSUTF8StringEncoding));
    return realBuild(opts);
};

pumpUntilAnswered = function (dialog) {
    // Answer with the last button, the one the contract makes default.
    dialog.buttons[dialog.buttons.length - 1].performClick(null);
    $.NSRunLoop.currentRunLoop.runUntilDate(
        $.NSDate.dateWithTimeIntervalSinceNow(0.2));
};
"""


@pytest.mark.visible_dialog
class TestDialogArgvSeam:
    """The argv contract, driven end to end through the real builder.

    `_dialog_argv` and `run(argv)` are two independent spellings of one
    contract -- the `--caution` flag, the title / message / buttons
    order, and the clicked label on stdout. Nothing else pins them
    together: the Python tests mock the subprocess, and the other
    harness calls into dialog.js below `run`. A rename or a reorder on
    either side would leave both dialogs broken and the suite green.
    """

    def _drive(self, tmp_path: Path, argv: list[str]) -> Any:
        harness = tmp_path / "argv.js"
        harness.write_text(_ARGV_HARNESS.replace("%DIALOG_JS%", str(DIALOG_JS)))
        # The production argv verbatim, with the harness standing in for
        # the script it names. Found by name rather than by position, so
        # a leading osascript option added later cannot silently point
        # this at the wrong entry.
        driven = [str(harness) if a.endswith("dialog.js") else a for a in argv]
        assert str(harness) in driven, argv
        return subprocess.run(
            driven, capture_output=True, text=True, timeout=120, check=False
        )

    def test_approval_argv_round_trips(self, tmp_path: Path) -> None:
        buttons = ["Cancel Job", "Delay Job", "Run Job"]
        argv = darwin._dialog_argv(
            "crony: synthetic job", "go?", buttons, caution=False
        )
        proc = self._drive(tmp_path, argv)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stderr) == {
            "title": "crony: synthetic job",
            "message": "go?",
            "buttons": buttons,
            "caution": False,
        }
        # Exactly what DarwinHost.show_dialog parses back.
        assert proc.stdout == "Run Job\n"

    def test_failure_argv_round_trips(self, tmp_path: Path) -> None:
        argv = darwin._dialog_argv(
            "crony: synthetic job fail (exit 2)",
            "Job:        synthetic",
            ["OK"],
            caution=True,
        )
        proc = self._drive(tmp_path, argv)
        assert proc.returncode == 0, proc.stderr
        # The caution flag is consumed as a flag, not mistaken for the
        # title, and the single button still comes back.
        assert json.loads(proc.stderr) == {
            "title": "crony: synthetic job fail (exit 2)",
            "message": "Job:        synthetic",
            "buttons": ["OK"],
            "caution": True,
        }
        assert proc.stdout == "OK\n"


class TestDialogUsage:
    """A malformed invocation fails loudly instead of showing an
    unanswerable window."""

    def test_too_few_arguments_exits_nonzero(self) -> None:
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", str(DIALOG_JS), "only-a-title"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode != 0
        assert "usage:" in proc.stderr


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _script_path, REPO_ROOT)
