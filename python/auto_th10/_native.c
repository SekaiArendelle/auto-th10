#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdlib.h>

#include "auto_th10/auto_th10.h"

typedef struct py_th10_session {
    PyObject_HEAD
    th10_session *session;
    th10_snapshot snapshot;
} py_th10_session;

static int raise_open_result(th10_open_result result) {
    switch (result.tag) {
        case TH10_OPEN_WINDOW_NOT_FOUND:
            PyErr_SetString(PyExc_RuntimeError, "TH10 game window not found");
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
            PyErr_SetString(PyExc_RuntimeError, "invalid th10_open result");
            break;
    }
    return -1;
}

static int raise_input_result(th10_input_result result) {
    switch (result.tag) {
        case TH10_INPUT_INVALID_SESSION:
            PyErr_SetString(PyExc_RuntimeError, "session is closed");
            break;
        case TH10_INPUT_UNSUPPORTED_ACTION:
            PyErr_Format(PyExc_ValueError, "unsupported action bits: 0x%x",
                         result.value.unsupported_action.unsupported_bits);
            break;
        case TH10_INPUT_SEND_FAILED:
            PyErr_Format(PyExc_OSError,
                         "SendInput inserted %u of %u events (Win32 error %u; error may be zero "
                         "when blocked by UIPI)",
                         result.value.send_failed.inserted_count,
                         result.value.send_failed.requested_count,
                         result.value.send_failed.win32_error);
            break;
        default:
            PyErr_SetString(PyExc_RuntimeError, "invalid th10_set_input result");
            break;
    }
    return -1;
}

static int raise_close_result(th10_close_result result) {
    switch (result.tag) {
        case TH10_CLOSE_INVALID_SESSION:
            PyErr_SetString(PyExc_RuntimeError, "session is already closed");
            break;
        case TH10_CLOSE_INPUT_RELEASE_FAILED:
            return raise_input_result(result.value.input_release_failed);
        case TH10_CLOSE_HANDLE_FAILED:
            PyErr_SetFromWindowsErr((int)result.value.handle_failed.win32_error);
            break;
        case TH10_CLOSE_INPUT_AND_HANDLE_FAILED:
            PyErr_Format(PyExc_OSError,
                         "input release failed with result tag %u and CloseHandle failed with "
                         "Win32 error %u",
                         (unsigned int)result.value.input_and_handle_failed.input.tag,
                         result.value.input_and_handle_failed.win32_error);
            break;
        default:
            PyErr_SetString(PyExc_RuntimeError, "invalid th10_close result");
            break;
    }
    return -1;
}

static int raise_snapshot_result(th10_snapshot_result result) {
    static const char *array_names[] = {"enemies", "enemy bullets", "enemy lasers", "resources"};
    switch (result.tag) {
        case TH10_SNAPSHOT_INVALID_ARGUMENT:
            PyErr_SetString(PyExc_RuntimeError, "invalid internal snapshot argument");
            break;
        case TH10_SNAPSHOT_NOT_IN_GAME:
            PyErr_SetString(PyExc_RuntimeError, "gameplay is not active");
            break;
        case TH10_SNAPSHOT_READ_FAILED:
            PyErr_Format(PyExc_OSError,
                         "ReadProcessMemory failed at 0x%llx: requested %zu bytes, read %zu "
                         "(Win32 error %u)",
                         (unsigned long long)result.value.read_failed.address,
                         result.value.read_failed.requested_size,
                         result.value.read_failed.bytes_read,
                         result.value.read_failed.win32_error);
            break;
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
            PyErr_SetString(PyExc_RuntimeError, "invalid th10_read_snapshot result");
            break;
    }
    return -1;
}

static int raise_capture_result(th10_capture_result result) {
    switch (result.tag) {
        case TH10_CAPTURE_INVALID_ARGUMENT:
            PyErr_SetString(PyExc_ValueError, "capture needs a path and an open game window");
            break;
        case TH10_CAPTURE_PRINT_WINDOW_FAILED:
            PyErr_SetString(PyExc_RuntimeError, "PrintWindow could not render the game window");
            break;
        case TH10_CAPTURE_CLIENT_RECT_FAILED:
        case TH10_CAPTURE_CREATE_DC_FAILED:
        case TH10_CAPTURE_CREATE_BITMAP_FAILED:
        case TH10_CAPTURE_FILE_OPEN_FAILED:
        case TH10_CAPTURE_FILE_WRITE_FAILED:
            PyErr_SetFromWindowsErr((int)result.win32_error);
            break;
        default:
            PyErr_SetString(PyExc_RuntimeError, "invalid th10_capture result");
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
        PyErr_SetString(PyExc_RuntimeError, "session is closed");
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

static PyObject *session_focus(py_th10_session *self, PyObject *ignored) {
    th10_focus_result result;
    (void)ignored;
    if (ensure_open(self) < 0) {
        return NULL;
    }
    result = th10_focus(self->session);
    switch (result.tag) {
        case TH10_FOCUS_SUCCESS:
            Py_RETURN_NONE;
        case TH10_FOCUS_INVALID_SESSION:
            PyErr_SetString(PyExc_RuntimeError, "session is closed");
            return NULL;
        case TH10_FOCUS_REJECTED:
            PyErr_SetString(PyExc_RuntimeError, "Windows rejected the foreground-window request");
            return NULL;
        case TH10_FOCUS_PERMISSION_AND_SET_REJECTED:
            PyErr_Format(PyExc_OSError,
                         "AllowSetForegroundWindow failed with Win32 error %u and Windows rejected "
                         "the foreground-window request",
                         result.value.permission_and_set_rejected.win32_error);
            return NULL;
        default:
            PyErr_SetString(PyExc_RuntimeError, "invalid th10_focus result");
            return NULL;
    }
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

static PyObject *session_state(py_th10_session *self, PyObject *ignored) {
    static const char *names[] = {
        "TH10_STATE_UNKNOWN", "TH10_STATE_MENU",   "TH10_STATE_PLAYING",
        "TH10_STATE_PAUSED",  "TH10_STATE_GAME_OVER",
    };
    th10_state state;
    (void)ignored;

    if (ensure_open(self) < 0) {
        return NULL;
    }
    state = th10_read_state(self->session);
    if ((unsigned int)state >= sizeof(names) / sizeof(names[0])) {
        PyErr_SetString(PyExc_RuntimeError, "invalid th10_read_state result");
        return NULL;
    }
    return PyUnicode_FromString(names[(unsigned int)state]);
}

static PyObject *session_record_broken(py_th10_session *self, PyObject *ignored) {
    (void)ignored;

    if (ensure_open(self) < 0) {
        return NULL;
    }
    if (th10_read_record_broken(self->session)) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static PyObject *session_stage_frames(py_th10_session *self, PyObject *ignored) {
    uint32_t frames = 0;
    (void)ignored;

    if (ensure_open(self) < 0) {
        return NULL;
    }
    if (!th10_read_stage_frames(self->session, &frames)) {
        PyErr_SetString(PyExc_OSError, "the stage frame counter could not be read");
        return NULL;
    }
    return PyLong_FromUnsignedLong((unsigned long)frames);
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
        raise_capture_result(result);
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
    {"snapshot", (PyCFunction)session_snapshot, METH_NOARGS, "Read one complete gameplay snapshot."},
    {"state", (PyCFunction)session_state, METH_NOARGS,
     "Report the screen: TH10_STATE_MENU / PLAYING / PAUSED / GAME_OVER / UNKNOWN."},
    {"record_broken", (PyCFunction)session_record_broken, METH_NOARGS,
     "Report whether the run set a new high score, so a restart owes a name entry."},
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
