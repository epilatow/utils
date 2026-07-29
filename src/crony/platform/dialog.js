// This is AI generated code

/*
 * crony's desktop dialogs: the interactive approval prompt and the
 * job-failure popup.
 *
 * Built as a Cocoa window through osascript's ObjC bridge rather than
 * with StandardAdditions' `display dialog`, for one reason: focus. A
 * `display dialog` activates the osascript process, which takes the
 * keyboard from whatever the user is typing in and points its buttons'
 * Return / Escape equivalents at that in-flight typing -- so a keystroke
 * aimed at an editor answers the dialog instead, running or cancelling
 * a job or dismissing a failure report unread. StandardAdditions offers
 * no way to suppress that.
 *
 * This is a non-activating panel, ordered in without ever being made
 * key, so keystrokes keep going to the application the user was working
 * in. Its key equivalents stay inert for the dialog's whole life: the
 * panel takes key status only for a view that needs to be first
 * responder, and it holds nothing but buttons, which answer a click.
 * (That click does land on the first press -- the buttons accept a
 * first mouse -- so nothing here needs clicking twice.)
 * Being noticed is left to passive signals: a floating window
 * level, a Space-joining collection behavior, and a bouncing Dock
 * icon. Not activating is not on its own enough -- see WINDOW_STYLE and
 * pumpUntilAnswered, which are the two places that guarantee is kept.
 *
 * Usage: osascript -l JavaScript dialog.js [--caution] <title>
 *        <message> <button>...
 *
 * Buttons are ordered first..last, matching the HostPlatform
 * show_dialog contract: the last is the default (Return) and, when
 * there is more than one, the first is the cancel button (Escape).
 * --caution adds the system caution icon, for the failure popup.
 *
 * Prints the clicked button's label, or an empty line when the window
 * is closed without a click. Arguments arrive as argv, so no text is
 * ever interpolated into a script literal and nothing needs escaping.
 *
 * One JXA idiom runs through this file and is easy to mistake for dead
 * code: a bare property access invokes a zero-argument ObjC method, so
 * `button.sizeToFit;`, `win.center;`, and `win.orderFrontRegardless;`
 * are calls, not no-op expression statements. Adding parentheses to
 * them does not work, and deleting them as unused silently removes
 * the behavior they perform.
 */

ObjC.import("Cocoa");

const WINDOW_MARGIN = 20;
const MIN_CONTENT_WIDTH = 420;
const BUTTON_MIN_WIDTH = 90;
const BUTTON_PADDING = 24;
const BUTTON_SPACING = 12;
const BUTTON_ROW_BOTTOM = 16;
const LABEL_GAP = 16;
const ICON_SIZE = 48;
const ICON_GAP = 14;

// NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
// NSWindowStyleMaskNonactivatingPanel.
//
// The non-activating panel bit is the load-bearing one, and it is why
// this is an NSPanel rather than an NSWindow: a plain window can become
// key, and merely declining to activate does not prevent the window
// server from routing keystrokes to it. A non-activating panel cannot
// take the keyboard from the application in front, so the guarantee
// holds however the window is ordered in, rather than resting on this
// file never calling an activating method.
const WINDOW_STYLE = 1 | 2 | 128;
const NS_BACKING_STORE_BUFFERED = 2;
const NS_FLOATING_WINDOW_LEVEL = 3;
const NS_APPLICATION_ACTIVATION_POLICY_REGULAR = 0;
const NS_CRITICAL_REQUEST = 0;
const NS_WINDOW_COLLECTION_BEHAVIOR_CAN_JOIN_ALL_SPACES = 1;
const NS_WINDOW_COLLECTION_BEHAVIOR_FULL_SCREEN_AUXILIARY = 256;

// How long the event pump waits for one event before re-checking
// whether the dialog has been answered. The slice is spent asleep
// waiting on the event queue, not spinning.
const RUN_LOOP_SLICE_S = 0.25;

// Every event type, for the pump's mask. NSEventMaskAny is
// NSUIntegerMax, which JavaScript cannot represent exactly and the
// bridge does not expose as a number; the largest exact integer covers
// bits 0-52, well past the highest NSEventType there is. An event
// whose bit fell outside the mask would sit in the queue undelivered.
const EVENT_MASK_ALL = Number.MAX_SAFE_INTEGER;

const HANDLER_CLASS = "CronyDialogHandler";

// Every panel this process builds, kept for its lifetime. Splitting
// the build from the presentation left a window that nothing on the
// ObjC side owned yet -- not the window server, since it was not on
// screen -- and reading its views back after a run-loop turn gave
// undefined. An NSMutableArray retains what it holds, so adding the
// panel to one settles the ownership rather than relying on when the
// bridge happens to release.
const RETAINED = $.NSMutableArray.array;

// The answer, shared with the ObjC handler methods below by closure:
// the clicked label, and whether the dialog is finished either way.
let clickedLabel = "";
let answered = false;
let handlerRegistered = false;

/*
 * Register the target/action class once per process. A button's action
 * and the window's close notification both land here, so a dismissed
 * window ends the wait exactly as a click does -- with no label, which
 * is the "no choice" crony reads as a cancel.
 */
function registerHandler() {
  if (handlerRegistered) {
    return;
  }
  ObjC.registerSubclass({
    name: HANDLER_CLASS,
    superclass: "NSObject",
    protocols: ["NSWindowDelegate"],
    methods: {
      "choose:": {
        types: ["void", ["id"]],
        implementation: function (sender) {
          clickedLabel = ObjC.unwrap(sender.title);
          answered = true;
        },
      },
      "windowWillClose:": {
        types: ["void", ["id"]],
        implementation: function () {
          answered = true;
        },
      },
    },
  });
  handlerRegistered = true;
}

/*
 * Fit each button to its own label, so a three-button approval prompt
 * and a one-button failure popup are both laid out from what they
 * actually say rather than from a guessed width.
 */
function makeButtons(titles, handler) {
  return titles.map(function (title) {
    const button = $.NSButton.buttonWithTitleTargetAction(
      title,
      handler,
      "choose:",
    );
    button.sizeToFit;
    const fitted = button.frame.size.width + BUTTON_PADDING;
    button.setFrameSize(
      $.NSMakeSize(
        Math.max(fitted, BUTTON_MIN_WIDTH),
        button.frame.size.height,
      ),
    );
    return button;
  });
}

/*
 * Size the window around its content rather than to a fixed shape: the
 * failure popup's body is a multi-line run summary and the approval
 * prompt's is one short line, and neither may be clipped -- a report
 * the user cannot read is the failure this dialog exists to deliver.
 *
 * The label is measured at the width it will actually wrap to, so the
 * height accounts for the caution icon's inset and for buttons wider
 * than the minimum content width.
 */
function layout(label, buttonsWidth, buttonHeight, caution) {
  const contentWidth = Math.max(
    MIN_CONTENT_WIDTH,
    buttonsWidth + 2 * WINDOW_MARGIN,
  );
  const labelLeft = WINDOW_MARGIN + (caution ? ICON_SIZE + ICON_GAP : 0);
  const labelWidth = contentWidth - WINDOW_MARGIN - labelLeft;
  label.preferredMaxLayoutWidth = labelWidth;
  const labelHeight = Math.ceil(label.fittingSize.height);
  const labelBottom = BUTTON_ROW_BOTTOM + buttonHeight + LABEL_GAP;
  const iconFloor = labelBottom + ICON_SIZE + WINDOW_MARGIN;
  const contentHeight = Math.max(
    labelBottom + labelHeight + WINDOW_MARGIN,
    caution ? iconFloor : 0,
  );
  return {
    contentWidth: contentWidth,
    contentHeight: contentHeight,
    labelFrame: $.NSMakeRect(
      labelLeft,
      contentHeight - WINDOW_MARGIN - labelHeight,
      labelWidth,
      labelHeight,
    ),
    iconFrame: $.NSMakeRect(
      WINDOW_MARGIN,
      contentHeight - WINDOW_MARGIN - ICON_SIZE,
      ICON_SIZE,
      ICON_SIZE,
    ),
  };
}

/*
 * Build the dialog, without putting it on screen -- presentDialog does
 * that. Returns the panel, its buttons, the computed geometry, and the
 * handler: the buttons and geometry so a test can drive a click and
 * check the layout without a user, and the handler because the panel
 * refers to its delegate weakly.
 */
function buildDialog(opts) {
  registerHandler();
  clickedLabel = "";
  answered = false;

  const app = $.NSApplication.sharedApplication;
  app.setActivationPolicy(NS_APPLICATION_ACTIVATION_POLICY_REGULAR);

  const handler = $[HANDLER_CLASS].alloc.init;
  const buttons = makeButtons(opts.buttons, handler);
  let buttonsWidth = BUTTON_SPACING * (buttons.length - 1);
  let buttonHeight = 0;
  buttons.forEach(function (button) {
    buttonsWidth += button.frame.size.width;
    buttonHeight = Math.max(buttonHeight, button.frame.size.height);
  });

  const label = $.NSTextField.wrappingLabelWithString(opts.message);
  const geometry = layout(label, buttonsWidth, buttonHeight, opts.caution);

  const win = $.NSPanel.alloc.initWithContentRectStyleMaskBackingDefer(
    $.NSMakeRect(0, 0, geometry.contentWidth, geometry.contentHeight),
    WINDOW_STYLE,
    NS_BACKING_STORE_BUFFERED,
    false,
  );
  // Only a control that genuinely needs typing may pull key status,
  // and this panel has none -- its buttons answer a click. So the
  // keyboard stays with whatever the user is working in even while
  // the dialog is the frontmost thing on screen.
  win.becomesKeyOnlyIfNeeded = true;
  win.floatingPanel = true;
  // The panel owns its views once they are added as subviews below,
  // and the handler is retained here because the panel refers to its
  // delegate weakly.
  RETAINED.addObject(win);
  RETAINED.addObject(handler);
  win.title = opts.title;
  win.delegate = handler;
  win.center;

  label.setFrame(geometry.labelFrame);
  win.contentView.addSubview(label);

  if (opts.caution) {
    const icon = $.NSImageView.alloc.initWithFrame(geometry.iconFrame);
    icon.image = $.NSImage.imageNamed($.NSImageNameCaution);
    win.contentView.addSubview(icon);
  }

  // Laid out right to left so the default lands in the bottom-right
  // corner where a Cocoa dialog's default button belongs, whatever
  // the individual labels measure.
  let x = geometry.contentWidth - WINDOW_MARGIN;
  for (let i = buttons.length - 1; i >= 0; i--) {
    x -= buttons[i].frame.size.width;
    buttons[i].setFrameOrigin($.NSMakePoint(x, BUTTON_ROW_BOTTOM));
    x -= BUTTON_SPACING;
    win.contentView.addSubview(buttons[i]);
  }
  // Return would answer the default button and Escape the cancel
  // button, and neither is reachable here -- which is the point. Key
  // equivalents are only reached by a key window, and this panel
  // never becomes one: it takes key status only for a view needing
  // first responder, and it has none. AppKit goes further for the
  // default button specifically, disabling its Return equivalent
  // while the window is not key, so reading an empty key equivalent
  // back from it is that mechanism working rather than the assignment
  // failing. They are set anyway, so the intent is on the record if
  // the panel ever gains a field and starts taking key status.
  buttons[buttons.length - 1].keyEquivalent = "\r";
  if (buttons.length > 1) {
    buttons[0].keyEquivalent = "\u001b";
  }

  // RETAINED above is what keeps the window and handler alive; the
  // handler is returned as well so a caller can reach it, since the
  // panel holds its delegate weakly.
  return {
    window: win,
    buttons: buttons,
    geometry: geometry,
    handler: handler,
  };
}

/*
 * Put the built dialog on screen without activating.
 *
 * Separate from building it so a test can read the built panel before
 * it goes on screen -- the default button's Return equivalent is only
 * observable before AppKit disables it -- and can drive a click without
 * racing the presentation.
 */
function presentDialog(dialog) {
  // Join whatever Space is in front, full-screen ones included.
  // Without this a window from an inactive app stays on the desktop
  // Space, and the approval prompt is raised only after the presence
  // wait has seen continuous active input -- someone typing, quite
  // possibly in a full-screen editor. The dialog would then be
  // invisible exactly when it fires, with the wait blocking and the
  // Dock hidden, so nothing would reach the user at all. Activating
  // is what the old dialog used to drag the Space over, and is the
  // one thing this file will not do.
  dialog.window.collectionBehavior =
    NS_WINDOW_COLLECTION_BEHAVIOR_CAN_JOIN_ALL_SPACES |
    NS_WINDOW_COLLECTION_BEHAVIOR_FULL_SCREEN_AUXILIARY;
  dialog.window.level = NS_FLOATING_WINDOW_LEVEL;
  dialog.window.orderFrontRegardless;
  // The token lets a caller take the bounce back; the run itself
  // wants it to continue until the dialog is answered.
  return $.NSApplication.sharedApplication.requestUserAttention(
    NS_CRITICAL_REQUEST,
  );
}

/*
 * Wait for the dialog to be answered, however long that takes: an
 * approval prompt the user leaves sitting is a pending decision, not a
 * timeout.
 *
 * This must dequeue and dispatch events itself. Spinning the run loop
 * alone services input sources but never calls `sendEvent:`, so the
 * window server's events stay queued and the dialog draws but cannot
 * be clicked -- the button action never fires and the close box does
 * nothing. `nextEventMatchingMask:...dequeue:YES` plus `sendEvent:` is
 * what `-[NSApplication run]` does, done here so the loop can also
 * watch for the answer.
 *
 * `dialog` is referenced throughout so its window and handler stay
 * alive for the whole wait.
 */
function pumpUntilAnswered(dialog) {
  const app = $.NSApplication.sharedApplication;
  // Deliberately no `finishLaunching` here, though -[NSApplication
  // run] calls it before its own loop: for a regular-policy app that
  // is the step that performs the launch activation, and it lands
  // once the events below start flowing -- taking the keyboard from
  // whatever the user is typing in, which is the whole thing this
  // file exists to avoid. Dequeuing and dispatching does not need it.
  while (!answered && dialog.window.isVisible) {
    const event = app.nextEventMatchingMaskUntilDateInModeDequeue(
      EVENT_MASK_ALL,
      $.NSDate.dateWithTimeIntervalSinceNow(RUN_LOOP_SLICE_S),
      $.NSDefaultRunLoopMode,
      true,
    );
    if (!event.isNil()) {
      app.sendEvent(event);
    }
  }
}

// The label the dialog was answered with, for a test driving its own
// click; empty until a button is clicked.
function answer() {
  return clickedLabel;
}

function isAnswered() {
  return answered;
}

function run(argv) {
  const args = argv.slice();
  const caution = args[0] === "--caution";
  if (caution) {
    args.shift();
  }
  // A title, a message, and at least one button.
  if (args.length < 3) {
    throw new Error(
      "usage: dialog.js [--caution] <title> <message> <button>...",
    );
  }
  const dialog = buildDialog({
    title: args[0],
    message: args[1],
    buttons: args.slice(2),
    caution: caution,
  });
  presentDialog(dialog);
  pumpUntilAnswered(dialog);
  return clickedLabel;
}
