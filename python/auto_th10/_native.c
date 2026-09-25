#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <errno.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "auto_th10/auto_th10.h"

typedef struct py_th10_session {
    PyObject_HEAD
    th10_session *session;
    th10_snapshot snapshot;
} py_th10_session;

static PyObject *gameplay_not_active_error;
static PyObject *session_closed_error;
static PyObject *game_not_found_error;

/* Reports a Win32 failure that carries detail no OSError attribute can hold - an
 * address, a byte count, how many events SendInput took. The code rides in
 * winerror, so a caller can act on it without parsing the message, and CPython
 * derives errno from it exactly as it does for the Windows errors it raises
 * itself.
 *
 * A zero code is not a Win32 error and must not be dressed up as one: SendInput
 * reports success when UIPI blocks the input, and an empty client area reports
 * nothing at all (see the TH10_INPUT_SEND_FAILED and TH10_CAPTURE_CLIENT_RECT_FAILED
 * members in the public header). Those keep the message and say so, because a
 * winerror of None renders the exception as the unreadable "[WinError None] ...". */
static int raise_windows_error(uint32_t win32_error, const char *format, ...) {
    PyObject *message;
    PyObject *arguments;
    va_list list;

    va_start(list, format);
    message = PyUnicode_FromFormatV(format, list);
    va_end(list);
    if (message == NULL) {
        return -1;
    }
    if (win32_error == 0) {
        /* PyErr_Format() is checked against printf's conversions, which %U is
         * not, so the text is built first. */
        PyObject *text = PyUnicode_FromFormat("%U (Win32 reported no error)", message);
        Py_DECREF(message);
        if (text == NULL) {
            return -1;
        }
        PyErr_SetObject(PyExc_OSError, text);
        Py_DECREF(text);
        return -1;
    }
    arguments = Py_BuildValue("(iOOi)", 0, message, Py_None, (int)win32_error);
    Py_DECREF(message);
    if (arguments == NULL) {
        return -1;
    }
    PyErr_SetObject(PyExc_OSError, arguments);
    Py_DECREF(arguments);
    return -1;
}

/* The same report for a failed memory read, which is where the address and the
 * byte counts come from. All three entry points that read a word of game memory
 * describe a failure through this, so a Python caller sees the same exception
 * and the same detail whichever one it was. */
static int raise_read_failure(const th10_read_failure *failure) {
    return raise_windows_error(failure->win32_error,
                               "ReadProcessMemory failed at %p: requested %zu bytes, read %zu",
                               (void *)(uintptr_t)failure->address, failure->requested_size,
                               failure->bytes_read);
}

static int raise_write_failure(const th10_write_failure *failure) {
    return raise_windows_error(failure->win32_error,
                               "WriteProcessMemory failed at %p: requested %zu bytes, wrote %zu",
                               (void *)(uintptr_t)failure->address, failure->requested_size,
                               failure->bytes_written);
}

static int raise_open_result(th10_open_result result) {
    switch (result.tag) {
        case TH10_OPEN_WINDOW_NOT_FOUND:
            PyErr_SetString(game_not_found_error, "TH10 game window not found");
            break;
        case TH10_OPEN_ENUM_WINDOWS_FAILED:
        case TH10_OPEN_GET_PROCESS_ID_FAILED:
        case TH10_OPEN_PROCESS_FAILED:
            PyErr_SetFromWindowsErr((int)result.value.win32_error.code);
            break;
        case TH10_OPEN_OUT_OF_MEMORY:
            PyErr_NoMemory();
            break;
        default:
            PyErr_SetString(PyExc_SystemError, "invalid th10_open result");
            break;
    }
    return -1;
}

static int raise_input_result(th10_input_result result) {
    switch (result.tag) {
        case TH10_INPUT_INVALID_SESSION:
            PyErr_SetString(session_closed_error, "session is closed");
            break;
        case TH10_INPUT_UNSUPPORTED_ACTION:
            PyErr_Format(PyExc_ValueError, "unsupported action bits: 0x%x",
                         result.value.unsupported_action.unsupported_bits);
            break;
        case TH10_INPUT_SEND_FAILED:
            return raise_windows_error(result.value.send_failed.win32_error,
                                       "SendInput inserted %u of %u events",
                                       result.value.send_failed.inserted_count,
                                       result.value.send_failed.requested_count);
        case TH10_INPUT_BRIDGE_INCOMPATIBLE:
            PyErr_SetString(PyExc_RuntimeError,
                            "the running game does not match the verified TH10 1.00a input code");
            break;
        case TH10_INPUT_BRIDGE_FAILED:
            return raise_windows_error(result.value.bridge_failed.win32_error,
                                       "background input bridge operation %u failed",
                                       (unsigned int)result.value.bridge_failed.operation);
        default:
            PyErr_SetString(PyExc_SystemError, "invalid th10_set_input result");
            break;
    }
    return -1;
}

static int raise_close_result(th10_close_result result) {
    switch (result.tag) {
        case TH10_CLOSE_INVALID_SESSION:
            PyErr_SetString(session_closed_error, "session is already closed");
            break;
        case TH10_CLOSE_INPUT_RELEASE_FAILED:
            return raise_input_result(result.value.input_release_failed);
        case TH10_CLOSE_HANDLE_FAILED:
            PyErr_SetFromWindowsErr((int)result.value.handle_failed.win32_error);
            break;
        case TH10_CLOSE_INPUT_AND_HANDLE_FAILED:
            return raise_windows_error(result.value.input_and_handle_failed.win32_error,
                                       "CloseHandle failed with Win32 error %u after releasing the "
                                       "input failed with result tag %u",
                                       result.value.input_and_handle_failed.win32_error,
                                       (unsigned int)result.value.input_and_handle_failed.input.tag);
        default:
            PyErr_SetString(PyExc_SystemError, "invalid th10_close result");
            break;
    }
    return -1;
}

static int raise_snapshot_result(th10_snapshot_result result) {
    static const char *array_names[] = {"enemies", "enemy bullets", "enemy lasers", "resources"};
    switch (result.tag) {
        case TH10_SNAPSHOT_INVALID_ARGUMENT:
            /* The binding passes its own session and its own snapshot, so this
             * can only mean the two layers disagree about the struct. */
            PyErr_SetString(PyExc_SystemError, "invalid internal snapshot argument");
            break;
        case TH10_SNAPSHOT_NOT_IN_GAME:
            PyErr_SetString(gameplay_not_active_error, "gameplay is not active");
            break;
        case TH10_SNAPSHOT_READ_FAILED:
            return raise_read_failure(&result.value.read_failed);
        case TH10_SNAPSHOT_ALLOCATION_FAILED: {
            const unsigned int array = (unsigned int)result.value.allocation_failed.array;
            const char *name = array < sizeof(array_names) / sizeof(array_names[0])
                                   ? array_names[array]
                                   : "unknown";
            PyErr_Format(PyExc_MemoryError, "could not grow %s array to %zu elements of %zu bytes",
                         name, result.value.allocation_failed.requested_capacity,
                         result.value.allocation_failed.element_size);
            break;
        }
        default:
            PyErr_SetString(PyExc_SystemError, "invalid th10_read_snapshot result");
            break;
    }
    return -1;
}

static int raise_write_result(th10_write_result result) {
    switch (result.tag) {
        case TH10_WRITE_INVALID_SESSION:
            /* Unreachable behind ensure_open(), like the same tag in every other
             * mapping here: the tag means "no usable session", not "the binding
             * and the core disagree". */
            PyErr_SetString(session_closed_error, "session is closed");
            break;
        case TH10_WRITE_UNSUPPORTED_SCREEN:
            /* A stage keeps no cursor, and neither does a screen this binding has
             * not measured: there is nothing here to move, and saying so is not an
             * error in the caller's arguments. */
            PyErr_SetString(PyExc_RuntimeError, "the screen the game is driving keeps no cursor");
            break;
        case TH10_WRITE_INVALID_ARGUMENT:
            PyErr_Format(PyExc_ValueError, "entry %d is outside the cursor's range 0..%d",
                         (int)result.value.invalid_argument.entry,
                         (int)result.value.invalid_argument.count - 1);
            break;
        case TH10_WRITE_READ_FAILED:
            return raise_read_failure(&result.value.read_failed);
        case TH10_WRITE_FAILED:
            return raise_write_failure(&result.value.write_failed);
        default:
            PyErr_SetString(PyExc_SystemError, "invalid th10_write_screen_cursor result");
            break;
    }
    return -1;
}

static int raise_frames_result(th10_frames_result result) {
    switch (result.tag) {
        case TH10_FRAMES_INVALID_SESSION:
            /* Unreachable behind ensure_open(), like the same tag in every other
             * mapping here: the tag means "no usable session", not "the binding
             * and the core disagree". */
            PyErr_SetString(session_closed_error, "session is closed");
            break;
        case TH10_FRAMES_READ_FAILED:
            return raise_read_failure(&result.value.read_failed);
        default:
            PyErr_SetString(PyExc_SystemError, "invalid th10_read_stage_frames result");
            break;
    }
    return -1;
}

/* The same for a file failure. Those carry errno rather than a Win32 error, and
 * they are about the path that was being written, which belongs on the exception
 * either way. A zero errno is not a cause - a short write is the usual way to get
 * one, and fwrite() need not set errno for it - so that case says what failed
 * instead of decoding 0. */
static int raise_file_error(uint32_t error_number, PyObject *path, const char *what) {
    PyObject *message;
    PyObject *arguments;

    if (error_number != 0) {
        errno = (int)error_number;
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
        return -1;
    }
    message = PyUnicode_FromFormat("the capture file could not be %s", what);
    if (message == NULL) {
        return -1;
    }
    arguments = Py_BuildValue("(iOO)", 0, message, path);
    Py_DECREF(message);
    if (arguments == NULL) {
        return -1;
    }
    PyErr_SetObject(PyExc_OSError, arguments);
    Py_DECREF(arguments);
    return -1;
}

static int raise_capture_result(th10_capture_result result, PyObject *path) {
    switch (result.tag) {
        case TH10_CAPTURE_INVALID_ARGUMENT:
            PyErr_SetString(PyExc_ValueError, "capture needs a path and an open game window");
            break;
        case TH10_CAPTURE_PRINT_WINDOW_FAILED:
            if (result.win32_error == 0) {
                PyErr_SetString(PyExc_RuntimeError, "PrintWindow could not render the game window");
            } else {
                PyErr_SetFromWindowsErr((int)result.win32_error);
            }
            break;
        case TH10_CAPTURE_CLIENT_RECT_FAILED:
            if (result.win32_error == 0) {
                PyErr_SetString(PyExc_RuntimeError, "the game window client area is empty");
            } else {
                PyErr_SetFromWindowsErr((int)result.win32_error);
            }
            break;
        case TH10_CAPTURE_CREATE_DC_FAILED:
        case TH10_CAPTURE_CREATE_BITMAP_FAILED:
            PyErr_SetFromWindowsErr((int)result.win32_error);
            break;
        case TH10_CAPTURE_FILE_OPEN_FAILED:
            return raise_file_error(result.win32_error, path, "opened");
        case TH10_CAPTURE_FILE_WRITE_FAILED:
            return raise_file_error(result.win32_error, path, "written");
        default:
            PyErr_SetString(PyExc_SystemError, "invalid th10_capture result");
            break;
    }
    return -1;
}

static PyObject *session_new(PyTypeObject *type, PyObject *args, PyObject *keywords) {
    py_th10_session *self;
    (void)args;
    (void)keywords;
    self = (py_th10_session *)type->tp_alloc(type, 0);
    if (self != NULL) {
        self->session = NULL;
        th10_snapshot_init(&self->snapshot);
    }
    return (PyObject *)self;
}

static int session_init(py_th10_session *self, PyObject *args, PyObject *keywords) {
    th10_open_result result;
    (void)keywords;
    if (!PyArg_ParseTuple(args, "")) {
        return -1;
    }
    if (self->session != NULL) {
        const th10_close_result close_result = th10_close(self->session);
        self->session = NULL;
        if (close_result.tag != TH10_CLOSE_SUCCESS) {
            return raise_close_result(close_result);
        }
    }
    result = th10_open();
    if (result.tag != TH10_OPEN_SUCCESS) {
        return raise_open_result(result);
    }
    self->session = result.value.session;
    return 0;
}

static void session_dealloc(py_th10_session *self) {
    if (self->session != NULL) {
        (void)th10_close(self->session);
    }
    self->session = NULL;
    th10_snapshot_destroy(&self->snapshot);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int ensure_open(py_th10_session *self) {
    if (self->session == NULL) {
        PyErr_SetString(session_closed_error, "session is closed");
        return -1;
    }
    return 0;
}

static PyObject *session_close(py_th10_session *self, PyObject *ignored) {
    th10_close_result result;
    (void)ignored;
    if (self->session == NULL) {
        Py_RETURN_NONE;
    }
    result = th10_close(self->session);
    self->session = NULL;
    if (result.tag != TH10_CLOSE_SUCCESS) {
        raise_close_result(result);
        return NULL;
    }
    Py_RETURN_NONE;
}

/* The focus failures, kept out of session_focus() so the mapping can be tested
 * without a game window to ask for the focus. */
static int raise_focus_result(th10_focus_result result) {
    switch (result.tag) {
        case TH10_FOCUS_INVALID_SESSION:
            PyErr_SetString(session_closed_error, "session is closed");
            break;
        case TH10_FOCUS_PERMISSION_AND_SET_REJECTED:
            return raise_windows_error(result.value.permission_and_set_rejected.win32_error,
                                       "AllowSetForegroundWindow failed and Windows rejected the "
                                       "foreground-window request");
        case TH10_FOCUS_REJECTED:
            PyErr_SetString(PyExc_RuntimeError, "Windows rejected the foreground-window request");
            break;
        default:
            PyErr_SetString(PyExc_SystemError, "invalid th10_focus result");
            break;
    }
    return -1;
}

static PyObject *session_focus(py_th10_session *self, PyObject *ignored) {
    th10_focus_result result;
    (void)ignored;
    if (ensure_open(self) < 0) {
        return NULL;
    }
    result = th10_focus(self->session);
    if (result.tag != TH10_FOCUS_SUCCESS) {
        raise_focus_result(result);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *session_set_input(py_th10_session *self, PyObject *argument) {
    unsigned long action_mask;
    th10_input_result result;
    if (ensure_open(self) < 0) {
        return NULL;
    }
    action_mask = PyLong_AsUnsignedLong(argument);
    if (PyErr_Occurred()) {
        return NULL;
    }
    result = th10_set_input(self->session, (uint32_t)action_mask);
    if (result.tag != TH10_INPUT_SUCCESS) {
        raise_input_result(result);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *session_enable_background_input(py_th10_session *self, PyObject *ignored) {
    th10_input_result result;
    (void)ignored;
    if (ensure_open(self) < 0) {
        return NULL;
    }
    result = th10_enable_background_input(self->session);
    if (result.tag != TH10_INPUT_SUCCESS) {
        raise_input_result(result);
        return NULL;
    }
    Py_RETURN_NONE;
}

static int dict_set_owned(PyObject *dictionary, const char *key, PyObject *value) {
    int result;
    if (value == NULL) {
        return -1;
    }
    result = PyDict_SetItemString(dictionary, key, value);
    Py_DECREF(value);
    return result;
}

static PyObject *build_rects(const th10_rect *items, size_t count) {
    PyObject *list = PyList_New((Py_ssize_t)count);
    size_t index;
    if (list == NULL) {
        return NULL;
    }
    for (index = 0; index < count; ++index) {
        PyObject *item = Py_BuildValue("(ffff)", (double)items[index].x, (double)items[index].y,
                                       (double)items[index].width, (double)items[index].height);
        if (item == NULL) {
            Py_DECREF(list);
            return NULL;
        }
        PyList_SET_ITEM(list, (Py_ssize_t)index, item);
    }
    return list;
}

static PyObject *build_bullets(const th10_enemy_bullet *items, size_t count) {
    PyObject *list = PyList_New((Py_ssize_t)count);
    size_t index;
    if (list == NULL) {
        return NULL;
    }
    for (index = 0; index < count; ++index) {
        PyObject *item = Py_BuildValue("(ffffff)", (double)items[index].x, (double)items[index].y,
                                       (double)items[index].width, (double)items[index].height,
                                       (double)items[index].dx, (double)items[index].dy);
        if (item == NULL) {
            Py_DECREF(list);
            return NULL;
        }
        PyList_SET_ITEM(list, (Py_ssize_t)index, item);
    }
    return list;
}

static PyObject *build_lasers(const th10_enemy_laser *items, size_t count) {
    PyObject *list = PyList_New((Py_ssize_t)count);
    size_t index;
    if (list == NULL) {
        return NULL;
    }
    for (index = 0; index < count; ++index) {
        PyObject *item = Py_BuildValue("(fffff)", (double)items[index].x, (double)items[index].y,
                                       (double)items[index].width, (double)items[index].height,
                                       (double)items[index].radian);
        if (item == NULL) {
            Py_DECREF(list);
            return NULL;
        }
        PyList_SET_ITEM(list, (Py_ssize_t)index, item);
    }
    return list;
}

static PyObject *build_points(const th10_point *items, size_t count) {
    PyObject *list = PyList_New((Py_ssize_t)count);
    size_t index;
    if (list == NULL) {
        return NULL;
    }
    for (index = 0; index < count; ++index) {
        PyObject *item = Py_BuildValue("(ff)", (double)items[index].x, (double)items[index].y);
        if (item == NULL) {
            Py_DECREF(list);
            return NULL;
        }
        PyList_SET_ITEM(list, (Py_ssize_t)index, item);
    }
    return list;
}

static PyObject *session_snapshot(py_th10_session *self, PyObject *ignored) {
    th10_snapshot_result snapshot_result;
    PyObject *result;
    (void)ignored;

    if (ensure_open(self) < 0) {
        return NULL;
    }
    snapshot_result = th10_read_snapshot(self->session, &self->snapshot);
    if (snapshot_result.tag != TH10_SNAPSHOT_SUCCESS) {
        raise_snapshot_result(snapshot_result);
        return NULL;
    }

    result = PyDict_New();
    if (result == NULL ||
        dict_set_owned(result, "player", Py_BuildValue("(ff)", (double)self->snapshot.player.x,
                                                        (double)self->snapshot.player.y)) < 0 ||
        dict_set_owned(result, "score", PyLong_FromUnsignedLong(self->snapshot.score)) < 0 ||
        dict_set_owned(result, "power", PyLong_FromUnsignedLong(self->snapshot.power)) < 0 ||
        dict_set_owned(result, "lives", PyLong_FromLong(self->snapshot.lives)) < 0 ||
        dict_set_owned(result, "game_over", PyBool_FromLong(self->snapshot.game_over)) < 0 ||
        dict_set_owned(result, "enemies",
                       build_rects(self->snapshot.enemies.data, self->snapshot.enemies.size)) < 0 ||
        dict_set_owned(result, "enemy_bullets",
                       build_bullets(self->snapshot.enemy_bullets.data,
                                     self->snapshot.enemy_bullets.size)) < 0 ||
        dict_set_owned(result, "enemy_lasers",
                       build_lasers(self->snapshot.enemy_lasers.data,
                                    self->snapshot.enemy_lasers.size)) < 0 ||
        dict_set_owned(result, "resources",
                       build_points(self->snapshot.resources.data, self->snapshot.resources.size)) < 0) {
        Py_XDECREF(result);
        result = NULL;
    }
    return result;
}

/* There is deliberately no binding for th10_read_state() here.
 *
 * It answers every question at once, and what it returns is coarser than the
 * reads that sit beside it: MENU is six screens under one value, a stage is
 * playing, paused and over alike, and the title screen's own demo reports
 * PLAYING. A Python agent gets more, sooner, from scene(), snapshot() and
 * screen(), which is why the episode lifecycle decides from those and this entry
 * point stays unbound. The state word is kept for people and for th10ctl; see
 * docs/game-ui.md. */

static PyObject *session_scene(py_th10_session *self, PyObject *ignored) {
    static const char *names[] = {
        "TH10_SCENE_UNKNOWN",
        "TH10_SCENE_MENU",
        "TH10_SCENE_STAGE",
    };
    th10_scene scene;
    (void)ignored;

    if (ensure_open(self) < 0) {
        return NULL;
    }
    scene = th10_read_scene(self->session);
    if ((unsigned int)scene >= sizeof(names) / sizeof(names[0])) {
        /* A scene outside the enumeration means the C header and this table
         * disagree, which is a bug here, not a game state. */
        PyErr_SetString(PyExc_SystemError, "invalid th10_read_scene result");
        return NULL;
    }
    return PyUnicode_FromString(names[(unsigned int)scene]);
}

static PyObject *session_set_screen_cursor(py_th10_session *self, PyObject *argument) {
    th10_write_result result;
    const long entry = PyLong_AsLong(argument);

    if (entry == -1 && PyErr_Occurred()) {
        return NULL;
    }
    if (ensure_open(self) < 0) {
        return NULL;
    }
    result = th10_write_screen_cursor(self->session, (int32_t)entry);
    if (result.tag != TH10_WRITE_SUCCESS) {
        raise_write_result(result);
        return NULL;
    }
    /* What the game reports afterwards, which is not always what was asked for. */
    return PyLong_FromLong((long)result.value.cursor);
}

static int raise_screen_result(th10_screen_result result) {
    switch (result.tag) {
        case TH10_SCREEN_INVALID_SESSION:
            /* Unreachable behind ensure_open(), like the same tag in every other
             * mapping here: the tag means "no usable session", not "the binding
             * and the core disagree". */
            PyErr_SetString(session_closed_error, "session is closed");
            break;
        case TH10_SCREEN_READ_FAILED:
            return raise_read_failure(&result.value.read_failed);
        default:
            PyErr_SetString(PyExc_SystemError, "invalid th10_read_screen result");
            break;
    }
    return -1;
}

static PyObject *session_screen(py_th10_session *self, PyObject *ignored) {
    static const char *names[] = {
        "TH10_SCREEN_KIND_UNKNOWN",
        "TH10_SCREEN_KIND_STAGE",
        "TH10_SCREEN_KIND_MENU",
        "TH10_SCREEN_KIND_PAUSE_MENU",
        "TH10_SCREEN_KIND_PAUSE_CONFIRM",
        "TH10_SCREEN_KIND_NAME_ENTRY",
    };
    th10_screen_result result;
    (void)ignored;

    if (ensure_open(self) < 0) {
        return NULL;
    }
    result = th10_read_screen(self->session);
    if (result.tag != TH10_SCREEN_SUCCESS) {
        raise_screen_result(result);
        return NULL;
    }
    if ((unsigned int)result.value.state.kind >= sizeof(names) / sizeof(names[0])) {
        /* A screen outside the enumeration means the C header and this table
         * disagree, which is a bug here, not a game state. */
        PyErr_SetString(PyExc_SystemError, "invalid th10_read_screen result");
        return NULL;
    }
    return Py_BuildValue(
        "(si)", names[(unsigned int)result.value.state.kind], (int)result.value.state.cursor);
}

static PyObject *session_stage_frames(py_th10_session *self, PyObject *ignored) {
    th10_frames_result result;
    (void)ignored;

    if (ensure_open(self) < 0) {
        return NULL;
    }
    result = th10_read_stage_frames(self->session);
    if (result.tag != TH10_FRAMES_SUCCESS) {
        raise_frames_result(result);
        return NULL;
    }
    return PyLong_FromUnsignedLong((unsigned long)result.value.frames);
}

static PyObject *session_capture(py_th10_session *self, PyObject *argument) {
    th10_capture_result result;
    wchar_t *path;

    if (ensure_open(self) < 0) {
        return NULL;
    }
    path = PyUnicode_AsWideCharString(argument, NULL);
    if (path == NULL) {
        return NULL;
    }
    result = th10_capture(self->session, path);
    PyMem_Free(path);
    if (result.tag != TH10_CAPTURE_SUCCESS) {
        raise_capture_result(result, argument);
        return NULL;
    }
    return Py_BuildValue("(II)", (unsigned int)result.width, (unsigned int)result.height);
}

static PyObject *session_enter(py_th10_session *self, PyObject *ignored) {
    (void)ignored;
    if (ensure_open(self) < 0) {
        return NULL;
    }
    return Py_NewRef((PyObject *)self);
}

static PyObject *session_exit(py_th10_session *self, PyObject *args) {
    (void)args;
    return session_close(self, NULL);
}

static PyMethodDef session_methods[] = {
    {"close", (PyCFunction)session_close, METH_NOARGS, "Release input and close the process handle."},
    {"focus", (PyCFunction)session_focus, METH_NOARGS, "Bring the game window to the foreground."},
    {"set_input", (PyCFunction)session_set_input, METH_O, "Set the currently held action mask."},
    {"enable_background_input", (PyCFunction)session_enable_background_input, METH_NOARGS,
     "Route input through the game process without taking keyboard focus."},
    {"snapshot", (PyCFunction)session_snapshot, METH_NOARGS, "Read one complete gameplay snapshot."},
    {"scene", (PyCFunction)session_scene, METH_NOARGS,
     "Report the screen family: TH10_SCENE_MENU / STAGE / UNKNOWN. One read, no wait."},
    {"screen", (PyCFunction)session_screen, METH_NOARGS,
     "Report the screen the game is driving and its cursor as (kind, cursor)."},
    {"set_screen_cursor", (PyCFunction)session_set_screen_cursor, METH_O,
     "Move that screen's cursor without a key press; returns what the game reports."},
    {"stage_frames", (PyCFunction)session_stage_frames, METH_NOARGS,
     "Read the stage frame counter: advances while playing, freezes while paused."},
    {"capture", (PyCFunction)session_capture, METH_O,
     "Capture the game window to a BMP path; returns (width, height)."},
    {"__enter__", (PyCFunction)session_enter, METH_NOARGS, NULL},
    {"__exit__", (PyCFunction)session_exit, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot session_slots[] = {
    {Py_tp_new, session_new},
    {Py_tp_init, session_init},
    {Py_tp_dealloc, session_dealloc},
    {Py_tp_methods, session_methods},
    {0, NULL},
};

static PyType_Spec session_spec = {
    .name = "auto_th10._native.Session",
    .basicsize = sizeof(py_th10_session),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = session_slots,
};

static PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_native",
    .m_doc = "Native Win32 bridge for auto-th10.",
    .m_size = -1,
};

/* Creates one exception, publishes it under its short name, and keeps the
 * reference in `slot` for the life of the process.
 *
 * The globals must hold a reference of their own: the mapping functions above
 * run long after PyInit__native() returns, and the module can be torn down - or
 * the interpreter finalized - while a Session still refers to one of these, which
 * would leave the pointer dangling. A single-phase module has nowhere to hand the
 * reference back, so it is released with the process. */
static int add_exception(PyObject *module, PyObject **slot, const char *name, PyObject *base) {
    const char *short_name = strrchr(name, '.');
    PyObject *exception;

    short_name = short_name == NULL ? name : short_name + 1;
    exception = PyErr_NewException(name, base, NULL);
    if (exception == NULL) {
        return -1;
    }
    if (PyModule_AddObjectRef(module, short_name, exception) < 0) {
        Py_DECREF(exception);
        return -1;
    }
    *slot = exception;
    return 0;
}

PyMODINIT_FUNC PyInit__native(void) {
    PyObject *module = PyModule_Create(&module_definition);
    PyObject *session_type;
    if (module == NULL) {
        return NULL;
    }
    session_type = PyType_FromSpec(&session_spec);
    if (session_type == NULL || PyModule_AddObjectRef(module, "Session", session_type) < 0) {
        Py_XDECREF(session_type);
        Py_DECREF(module);
        return NULL;
    }
    Py_DECREF(session_type);

    /* All three derive from RuntimeError, which is what they were before they
     * had names: a caller that only knows the old behaviour keeps working. */
    if (add_exception(module, &gameplay_not_active_error, "auto_th10.GameplayNotActive",
                      PyExc_RuntimeError) < 0 ||
        add_exception(module, &session_closed_error, "auto_th10.SessionClosedError",
                      PyExc_RuntimeError) < 0 ||
        add_exception(module, &game_not_found_error, "auto_th10.GameNotFound",
                      PyExc_RuntimeError) < 0) {
        Py_DECREF(module);
        return NULL;
    }

#define ADD_ACTION(name) \
    if (PyModule_AddIntConstant(module, #name, TH10_ACTION_##name) < 0) { \
        Py_DECREF(module); \
        return NULL; \
    }
    ADD_ACTION(NONE)
    ADD_ACTION(LEFT)
    ADD_ACTION(RIGHT)
    ADD_ACTION(UP)
    ADD_ACTION(DOWN)
    ADD_ACTION(SHOOT)
    ADD_ACTION(FOCUS)
    ADD_ACTION(BOMB)
    ADD_ACTION(ESCAPE)
#undef ADD_ACTION
    return module;
}
