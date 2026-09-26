/* Locks down the translation from C result tags to Python exceptions in
 * python/auto_th10/_native.c.
 *
 * That translation is the one part of the binding the Python suites cannot
 * reach: they drive a real Session, so they only ever produce the failures a
 * running game and a real file system produce, and every other tag in the public
 * header goes untested. Here the C results are built by hand and handed to the
 * mapping functions, so both the exception a caller catches and the attributes
 * it reads are pinned down tag by tag.
 *
 * The binding is included rather than linked because the mapping functions are
 * static, which is what keeps them out of the module's namespace. An interpreter
 * is started to give them somewhere to report into, and PyInit__native() is
 * called so the module - and the exception objects the mapping uses - exist the
 * way CPython creates them.
 *
 * Only two checks are left out, and both need a live Session rather than a
 * hand-built result: session_scene()'s unknown-scene error and session_screen()'s
 * unknown-screen one. */
#include "../../python/auto_th10/_native.c"

#include <stdio.h>

/* The C test that came before this one asserts, which a release build compiles
 * away; a mapping test that stops testing under NDEBUG would be worse than none,
 * so these checks count themselves and main() reports the count as its status. */
static int failed_checks;
static int run_checks;

/* The exception the last RAISE() produced. Checks consume the pending error, so
 * it is kept here for the checks that follow in the same case: one tag is
 * usually worth two of them, the type and an attribute. */
static PyObject *last_exception;

/* Spelled as a statement so a case reads as "raise this, then check what came
 * out". Every mapping function returns -1, which no case has a use for. */
#define RAISE(expression) \
    do { \
        Py_CLEAR(last_exception); \
        (void)(expression); \
    } while (0)

static void report_failure(const char *description, const char *detail) {
    fprintf(stderr, "FAIL %s: %s\n", description, detail);
}

/* The pending exception, with the previous one cleared. NULL when there was none. */
static PyObject *take_exception(void) {
    PyObject *type = NULL;
    PyObject *value = NULL;
    PyObject *traceback = NULL;

    PyErr_Fetch(&type, &value, &traceback);
    PyErr_NormalizeException(&type, &value, &traceback);
    Py_XDECREF(traceback);
    Py_XDECREF(type);
    return value;
}

/* Checks the pending exception once - that it is an instance of `expected` - and
 * remembers it for the checks that follow in the same case. Returns it borrowed,
 * or NULL after reporting. */
static PyObject *expected_exception(PyObject *expected, const char *description) {
    const char *raised;
    char detail[256];
    int matches;

    if (last_exception != NULL) {
        return last_exception;
    }
    last_exception = take_exception();
    run_checks++;
    if (last_exception == NULL) {
        report_failure(description, "no exception was raised");
        failed_checks++;
        return NULL;
    }
    matches = PyObject_IsInstance(last_exception, expected);
    if (matches < 0) {
        PyErr_Clear();
        matches = 0;
    }
    if (matches == 0) {
        raised = Py_TYPE(last_exception)->tp_name;
        snprintf(detail, sizeof(detail), "%s was raised, expected %s", raised,
                 ((PyTypeObject *)expected)->tp_name);
        report_failure(description, detail);
        failed_checks++;
    }
    return last_exception;
}

static void check_exception(PyObject *expected, const char *description) {
    (void)expected_exception(expected, description);
}

/* Asserts the attribute reads as `wanted` once stringified, which covers the
 * integer codes, a filename and the None of "no Win32 error" in one check. */
static void check_attribute(PyObject *expected, const char *attribute, const char *wanted,
                            const char *description) {
    PyObject *value = expected_exception(expected, description);
    PyObject *item;
    PyObject *text;
    const char *shown;
    char detail[256];

    if (value == NULL) {
        return;
    }
    item = PyObject_GetAttrString(value, attribute);
    if (item == NULL) {
        PyErr_Clear();
        snprintf(detail, sizeof(detail), "the exception has no %s", attribute);
        report_failure(description, detail);
        failed_checks++;
        return;
    }
    text = PyObject_Str(item);
    shown = text == NULL ? NULL : PyUnicode_AsUTF8(text);
    if (shown == NULL) {
        PyErr_Clear();
        shown = "?";
    }
    run_checks++;
    if (strcmp(shown, wanted) != 0) {
        snprintf(detail, sizeof(detail), "%s reads as %s, expected %s", attribute, shown, wanted);
        report_failure(description, detail);
        failed_checks++;
    }
    Py_XDECREF(text);
    Py_DECREF(item);
}

static void check_message(PyObject *expected, const char *needle, const char *description) {
    PyObject *value = expected_exception(expected, description);
    PyObject *text;
    PyObject *wanted;
    char detail[256];
    int found = 0;

    if (value == NULL) {
        return;
    }
    text = PyObject_Str(value);
    wanted = PyUnicode_FromString(needle);
    if (text != NULL && wanted != NULL) {
        found = PyUnicode_Contains(text, wanted);
        if (found < 0) {
            PyErr_Clear();
            found = 0;
        }
    } else {
        PyErr_Clear();
    }
    run_checks++;
    if (found == 0) {
        snprintf(detail, sizeof(detail), "the message does not mention %s", needle);
        report_failure(description, detail);
        failed_checks++;
    }
    Py_XDECREF(wanted);
    Py_XDECREF(text);
}

static void check_subclass(PyObject *derived, PyObject *base, const char *description) {
    run_checks++;
    if (PyObject_IsSubclass(derived, base) != 1) {
        PyErr_Clear();
        report_failure(description, "the exception does not derive from the expected base");
        failed_checks++;
    }
}

int main(void) {
    PyObject *module;
    PyObject *path;
    int status = 1;

    Py_Initialize();
    if (!Py_IsInitialized()) {
        fputs("FAIL the interpreter could not be started\n", stderr);
        return 1;
    }
    module = PyInit__native();
    if (module == NULL) {
        PyErr_Print();
        return 1;
    }
    path = PyUnicode_FromString("shot.bmp");
    if (path == NULL) {
        PyErr_Print();
        Py_DECREF(module);
        return 1;
    }

    /* The named exceptions are what the Python layer re-exports and what the
     * episode lifecycle catches by name, so they have to be RuntimeErrors: that
     * is what they were before they had names, and a caller that knows only the
     * old behaviour must keep working. */
    check_subclass(gameplay_not_active_error, PyExc_RuntimeError, "GameplayNotActive");
    check_subclass(session_closed_error, PyExc_RuntimeError, "SessionClosedError");
    check_subclass(game_not_found_error, PyExc_RuntimeError, "GameNotFound");

    /* th10_open() */
    RAISE(raise_open_result((th10_open_result){.tag = TH10_OPEN_WINDOW_NOT_FOUND}));
    check_exception(game_not_found_error, "open: the window was not found");
    RAISE(raise_open_result((th10_open_result){
        .tag = TH10_OPEN_ENUM_WINDOWS_FAILED,
        .value.win32_error = {.code = 5},
    }));
    check_attribute(PyExc_OSError, "winerror", "5", "open: EnumWindows failed");
    RAISE(raise_open_result((th10_open_result){
        .tag = TH10_OPEN_GET_PROCESS_ID_FAILED,
        .value.win32_error = {.code = 6},
    }));
    check_attribute(PyExc_OSError, "winerror", "6", "open: the window has no process");
    RAISE(raise_open_result((th10_open_result){
        .tag = TH10_OPEN_PROCESS_FAILED,
        .value.win32_error = {.code = 5},
    }));
    check_attribute(PyExc_OSError, "winerror", "5", "open: the process could not be opened");
    RAISE(raise_open_result((th10_open_result){.tag = TH10_OPEN_OUT_OF_MEMORY}));
    check_exception(PyExc_MemoryError, "open: out of memory");
    RAISE(raise_open_result((th10_open_result){.tag = (th10_open_result_tag)999}));
    check_exception(PyExc_SystemError, "open: an unknown tag is a binding bug");

    /* th10_set_input() */
    RAISE(raise_input_result((th10_input_result){.tag = TH10_INPUT_INVALID_SESSION}));
    check_exception(session_closed_error, "input: the session is closed");
    RAISE(raise_input_result((th10_input_result){
        .tag = TH10_INPUT_UNSUPPORTED_ACTION,
        .value.unsupported_action = {.unsupported_bits = 0x100},
    }));
    check_exception(PyExc_ValueError, "input: an unsupported mask is a ValueError");
    check_message(PyExc_ValueError, "unsupported action bits: 0x100",
                  "input: the offending bits are named");
    RAISE(raise_input_result((th10_input_result){
        .tag = TH10_INPUT_SEND_FAILED,
        .value.send_failed = {.requested_count = 4, .inserted_count = 3, .win32_error = 5},
    }));
    check_attribute(PyExc_OSError, "winerror", "5", "input: SendInput failed");
    check_message(PyExc_OSError, "3 of 4 events", "input: the counts are in the message");
    RAISE(raise_input_result((th10_input_result){
        .tag = TH10_INPUT_SEND_FAILED,
        .value.send_failed = {.requested_count = 3, .inserted_count = 2, .win32_error = 0},
    }));
    check_attribute(PyExc_OSError, "winerror", "None",
                    "input: UIPI reports no Win32 error, and winerror says so");
    RAISE(raise_input_result(
        (th10_input_result){.tag = TH10_INPUT_BRIDGE_INCOMPATIBLE}));
    check_exception(PyExc_RuntimeError, "input: an incompatible game is rejected");
    check_message(PyExc_RuntimeError, "original, th10chs, or th10cht",
                  "input: the verified executable set is named");
    RAISE(raise_input_result((th10_input_result){
        .tag = TH10_INPUT_BRIDGE_FAILED,
        .value.bridge_failed = {
            .operation = TH10_INPUT_BRIDGE_WRITE_PATCH,
            .win32_error = 5,
        },
    }));
    check_attribute(PyExc_OSError, "winerror", "5", "input: a bridge Win32 failure");
    check_message(PyExc_OSError, "operation 10", "input: the failed bridge step is named");
    RAISE(raise_input_result((th10_input_result){.tag = (th10_input_result_tag)999}));
    check_exception(PyExc_SystemError, "input: an unknown tag is a binding bug");

    /* th10_close() */
    RAISE(raise_close_result((th10_close_result){.tag = TH10_CLOSE_INVALID_SESSION}));
    check_exception(session_closed_error, "close: the session is already closed");
    RAISE(raise_close_result((th10_close_result){
        .tag = TH10_CLOSE_INPUT_RELEASE_FAILED,
        .value.input_release_failed = {.tag = TH10_INPUT_SEND_FAILED,
                                       .value.send_failed = {.requested_count = 2,
                                                             .inserted_count = 0,
                                                             .win32_error = 5}},
    }));
    check_attribute(PyExc_OSError, "winerror", "5", "close: a release failure keeps the detail");
    RAISE(raise_close_result((th10_close_result){
        .tag = TH10_CLOSE_HANDLE_FAILED,
        .value.handle_failed = {.win32_error = 6},
    }));
    check_attribute(PyExc_OSError, "winerror", "6", "close: CloseHandle failed");
    RAISE(raise_close_result((th10_close_result){
        .tag = TH10_CLOSE_INPUT_AND_HANDLE_FAILED,
        .value.input_and_handle_failed = {.input = {.tag = TH10_INPUT_SEND_FAILED},
                                          .win32_error = 5},
    }));
    check_attribute(PyExc_OSError, "winerror", "5", "close: both failures at once");
    check_message(PyExc_OSError, "releasing the input failed", "close: both failures are named");
    RAISE(raise_close_result((th10_close_result){.tag = (th10_close_result_tag)999}));
    check_exception(PyExc_SystemError, "close: an unknown tag is a binding bug");

    /* th10_read_snapshot() */
    RAISE(raise_snapshot_result((th10_snapshot_result){.tag = TH10_SNAPSHOT_INVALID_ARGUMENT}));
    check_exception(PyExc_SystemError, "snapshot: a rejected internal argument is a binding bug");
    RAISE(raise_snapshot_result((th10_snapshot_result){.tag = TH10_SNAPSHOT_NOT_IN_GAME}));
    check_exception(gameplay_not_active_error, "snapshot: no stage is loaded");
    RAISE(raise_snapshot_result((th10_snapshot_result){
        .tag = TH10_SNAPSHOT_READ_FAILED,
        .value.read_failed = {.address = 0x474C88u,
                              .requested_size = 4,
                              .bytes_read = 0,
                              .win32_error = 299},
    }));
    check_attribute(PyExc_OSError, "winerror", "299", "snapshot: a failed read carries the code");
    check_message(PyExc_OSError, "requested 4 bytes, read 0",
                  "snapshot: a failed read carries the byte counts");
    RAISE(raise_snapshot_result((th10_snapshot_result){
        .tag = TH10_SNAPSHOT_ALLOCATION_FAILED,
        .value.allocation_failed = {.array = TH10_SNAPSHOT_ENEMIES,
                                    .requested_capacity = 8,
                                    .element_size = 16},
    }));
    check_message(PyExc_MemoryError, "enemies", "snapshot: the failed array is named");
    RAISE(raise_snapshot_result((th10_snapshot_result){.tag = (th10_snapshot_result_tag)999}));
    check_exception(PyExc_SystemError, "snapshot: an unknown tag is a binding bug");

    /* th10_write_screen_cursor() */
    RAISE(raise_write_result((th10_write_result){.tag = TH10_WRITE_INVALID_SESSION}));
    check_exception(session_closed_error, "write: the session is closed");
    RAISE(raise_write_result((th10_write_result){.tag = TH10_WRITE_UNSUPPORTED_SCREEN}));
    check_exception(PyExc_RuntimeError, "write: a screen with no cursor is not an argument error");
    RAISE(raise_write_result((th10_write_result){
        .tag = TH10_WRITE_INVALID_ARGUMENT,
        .value.invalid_argument = {.entry = 91, .count = 91},
    }));
    check_exception(PyExc_ValueError, "write: an entry outside the list is a ValueError");
    check_message(PyExc_ValueError, "0..90", "write: the range it has is named");
    RAISE(raise_write_result((th10_write_result){
        .tag = TH10_WRITE_READ_FAILED,
        .value.read_failed = {.address = 0x477830u,
                              .requested_size = 4,
                              .bytes_read = 0,
                              .win32_error = 299},
    }));
    check_attribute(PyExc_OSError, "winerror", "299", "write: a failed read carries the code");
    RAISE(raise_write_result((th10_write_result){
        .tag = TH10_WRITE_FAILED,
        .value.write_failed = {.address = 0xA8334ECu,
                               .requested_size = 4,
                               .bytes_written = 0,
                               .win32_error = 5},
    }));
    check_attribute(PyExc_OSError, "winerror", "5", "write: a failed write carries the code");
    check_message(PyExc_OSError, "WriteProcessMemory", "write: the failed call is named");
    RAISE(raise_write_result((th10_write_result){.tag = (th10_write_result_tag)999}));
    check_exception(PyExc_SystemError, "write: an unknown tag is a binding bug");

    /* th10_read_screen() */
    RAISE(raise_screen_result((th10_screen_result){.tag = TH10_SCREEN_INVALID_SESSION}));
    check_exception(session_closed_error, "screen: the session is closed");
    RAISE(raise_screen_result((th10_screen_result){
        .tag = TH10_SCREEN_READ_FAILED,
        .value.read_failed = {.address = 0x477830u,
                              .requested_size = 4,
                              .bytes_read = 0,
                              .win32_error = 299},
    }));
    check_attribute(PyExc_OSError, "winerror", "299", "screen: a failed read carries the code");
    RAISE(raise_screen_result((th10_screen_result){.tag = (th10_screen_result_tag)999}));
    check_exception(PyExc_SystemError, "screen: an unknown tag is a binding bug");

    /* th10_read_stage_frames() */
    RAISE(raise_frames_result((th10_frames_result){
        .tag = TH10_FRAMES_READ_FAILED,
        .value.read_failed = {.address = 0x474C88u,
                              .requested_size = 4,
                              .bytes_read = 0,
                              .win32_error = 5},
    }));
    check_attribute(PyExc_OSError, "winerror", "5", "frames: a failed read carries the code");
    RAISE(raise_frames_result((th10_frames_result){.tag = TH10_FRAMES_INVALID_SESSION}));
    check_exception(session_closed_error, "frames: the session is closed");
    RAISE(raise_frames_result((th10_frames_result){.tag = (th10_frames_result_tag)999}));
    check_exception(PyExc_SystemError, "frames: an unknown tag is a binding bug");

    /* th10_focus() */
    RAISE(raise_focus_result((th10_focus_result){.tag = TH10_FOCUS_INVALID_SESSION}));
    check_exception(session_closed_error, "focus: the session is closed");
    RAISE(raise_focus_result((th10_focus_result){
        .tag = TH10_FOCUS_PERMISSION_AND_SET_REJECTED,
        .value.permission_and_set_rejected = {.win32_error = 5},
    }));
    check_attribute(PyExc_OSError, "winerror", "5", "focus: both refusals carry the code");
    check_message(PyExc_OSError, "AllowSetForegroundWindow",
                  "focus: the failed call is named");
    RAISE(raise_focus_result((th10_focus_result){
        .tag = TH10_FOCUS_PERMISSION_AND_SET_REJECTED,
        .value.permission_and_set_rejected = {.win32_error = 0},
    }));
    check_attribute(PyExc_OSError, "winerror", "None",
                    "focus: a refusal with no Win32 error says so instead of decoding 0");
    RAISE(raise_focus_result((th10_focus_result){.tag = TH10_FOCUS_REJECTED}));
    check_exception(PyExc_RuntimeError, "focus: a refused request is not an OSError");
    RAISE(raise_focus_result((th10_focus_result){.tag = (th10_focus_result_tag)999}));
    check_exception(PyExc_SystemError, "focus: an unknown tag is a binding bug");

    /* th10_capture() */
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_INVALID_ARGUMENT}, path));
    check_exception(PyExc_ValueError, "capture: a missing path is a ValueError");
    /* A window that draws nothing reports no Win32 error at all, so there is no
     * code to carry and this stays the plain RuntimeError it always was. */
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_PRINT_WINDOW_FAILED,
                                                     .win32_error = 0},
                               path));
    check_exception(PyExc_RuntimeError, "capture: a window that draws nothing is not an OSError");
    check_message(PyExc_RuntimeError, "PrintWindow", "capture: the failed call is named");
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_PRINT_WINDOW_FAILED,
                                                     .win32_error = 5},
                               path));
    check_attribute(PyExc_OSError, "winerror", "5", "capture: PrintWindow failed");
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_CLIENT_RECT_FAILED,
                                                     .win32_error = 0},
                               path));
    check_exception(PyExc_RuntimeError, "capture: an empty client area is not a Win32 error");
    check_message(PyExc_RuntimeError, "client area", "capture: an empty client area is named");
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_CLIENT_RECT_FAILED,
                                                     .win32_error = 5},
                               path));
    check_attribute(PyExc_OSError, "winerror", "5", "capture: a failed measurement carries the code");
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_CREATE_DC_FAILED,
                                                     .win32_error = 5},
                               path));
    check_attribute(PyExc_OSError, "winerror", "5", "capture: the device context failed");
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_CREATE_BITMAP_FAILED,
                                                     .win32_error = 5},
                               path));
    check_attribute(PyExc_OSError, "winerror", "5", "capture: the bitmap failed");
    /* The two file tags carry errno, not a Win32 error, and the Python side has
     * to decode them that way: EACCES is errno 13, which as a Win32 code would
     * read as "the data is invalid". */
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_FILE_OPEN_FAILED,
                                                     .win32_error = (uint32_t)EACCES},
                               path));
    check_attribute(PyExc_OSError, "errno", "13", "capture: a file error keeps its errno");
    check_attribute(PyExc_OSError, "filename", "shot.bmp", "capture: the file error names the file");
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_FILE_WRITE_FAILED,
                                                     .win32_error = (uint32_t)EACCES},
                               path));
    check_attribute(PyExc_OSError, "errno", "13", "capture: a write error keeps its errno too");
    /* A short write is the failure that has no errno to report; the path still
     * belongs on the exception, and the message says what failed instead of
     * decoding a zero. */
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_FILE_OPEN_FAILED,
                                                     .win32_error = 0},
                               path));
    check_message(PyExc_OSError, "could not be opened",
                  "capture: a file error with no errno says so");
    check_attribute(PyExc_OSError, "filename", "shot.bmp",
                    "capture: a file error names the file with or without an errno");
    RAISE(raise_capture_result((th10_capture_result){.tag = TH10_CAPTURE_FILE_WRITE_FAILED,
                                                     .win32_error = 0},
                               path));
    check_message(PyExc_OSError, "could not be written",
                  "capture: a short write has no errno to report");
    check_attribute(PyExc_OSError, "filename", "shot.bmp",
                    "capture: a short write still names the file");
    RAISE(raise_capture_result((th10_capture_result){.tag = (th10_capture_result_tag)999}, path));
    check_exception(PyExc_SystemError, "capture: an unknown tag is a binding bug");

    Py_CLEAR(last_exception);
    Py_DECREF(path);
    Py_DECREF(module);
    (void)Py_FinalizeEx();

    if (failed_checks != 0) {
        fprintf(stderr, "native exception mapping: %d of %d checks failed\n", failed_checks,
                run_checks);
        status = 1;
    } else {
        printf("native exception mapping: %d checks passed\n", run_checks);
        status = 0;
    }
    return status;
}
