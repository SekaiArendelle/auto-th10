/**
 * @file auto_th10_module.cpp
 * @brief Python C API bindings for auto_th10 classes (C++23, no exceptions).
 *
 * Exposed Python types:
 *  - auto_th10.ThObject(x: float, y: float)
 *      .x, .y  (read/write)
 *
 *  - auto_th10.Player(x: float, y: float) : ThObject
 *      .x, .y  (read/write, from base)
 *      Player.width, Player.height  (class attributes, read-only; values from C++)
 *
 *  - auto_th10.Enemy(x: float, y: float, width: float, height: float) : ThObject
 *      .x, .y  (read/write, from base)
 *      .width, .height  (read/write)
 *
 *  - auto_th10.EnemyBullet(x: float, y: float, width: float, height: float, dx: float, dy: float) : ThObject
 *      .x, .y  (read/write, from base)
 *      .width, .height, .dx, .dy  (read/write)
 *
 *  - auto_th10.EnemyLaser(x: float, y: float, width: float, height: float, radian: float) : ThObject
 *      .x, .y  (read/write, from base)
 *      .width, .height, .radian  (read/write)
 *
 *  - auto_th10.Resource(x: float, y: float) : ThObject
 *      .x, .y  (read/write, from base)
 *      Resource.width, Resource.height  (class attributes, read-only; values from C++)
 */

#include <Python.h>
#include <new> // for std::nothrow
#include <auto_th10/thobjects.hh>
#include <auto_th10/process.hh>
#include <auto_th10/control.hh>

namespace {
/** Check if the game is over (hp == -1). */
PyObject* is_game_over(PyObject*, PyObject* args) noexcept {
    PyObject* hproc_obj = nullptr;
    if (!PyArg_ParseTuple(args, "O", &hproc_obj)) {
        return nullptr;
    }
    HANDLE hproc = reinterpret_cast<HANDLE>(PyLong_AsVoidPtr(hproc_obj));
    if (PyErr_Occurred())
        return nullptr;

    bool over = ::auto_th10::is_game_over(hproc);
    if (over) {
        Py_RETURN_TRUE;
    } else {
        Py_RETURN_FALSE;
    }
}


/** Bring window to foreground. */
PyObject* set_as_foreground(PyObject*, PyObject* args) noexcept {
    PyObject* hwnd_obj = nullptr;
    if (!PyArg_ParseTuple(args, "O", &hwnd_obj)) {
        return nullptr;
    }
    HWND hwnd = reinterpret_cast<HWND>(PyLong_AsVoidPtr(hwnd_obj));
    if (PyErr_Occurred())
        return nullptr;

    ::auto_th10::set_as_foreground(hwnd);
    Py_RETURN_NONE;
}

/** Get HWND of TH10 window */
PyObject* get_hwnd(PyObject*, PyObject*) noexcept {
    HWND hwnd = ::auto_th10::get_hwnd();
    return PyLong_FromVoidPtr(hwnd);
}

/** Get PID and TID from HWND */
PyObject* get_pid_and_tid(PyObject*, PyObject* args) noexcept {
    PyObject* hwnd_obj = nullptr;
    if (!PyArg_ParseTuple(args, "O", &hwnd_obj)) {
        return nullptr;
    }
    HWND hwnd = reinterpret_cast<HWND>(PyLong_AsVoidPtr(hwnd_obj));
    if (PyErr_Occurred())
        return nullptr;

    ::auto_th10::details::pid_and_tid pt = ::auto_th10::get_pid_and_tid(hwnd);
    return Py_BuildValue("(kk)", static_cast<unsigned long>(pt.pid), static_cast<unsigned long>(pt.tid));
}

/** Get process handle from PID */
PyObject* get_process_handle(PyObject*, PyObject* args) noexcept {
    unsigned long pid;
    if (!PyArg_ParseTuple(args, "k", &pid)) {
        return nullptr;
    }
    HANDLE hproc = ::auto_th10::get_process_handle(pid);
    if (hproc == nullptr) {
        Py_RETURN_NONE;
    }
    return PyLong_FromVoidPtr(hproc);
}

/* ===== Helpers ===== */

/**
 * @brief Convert a Python number to auto_th10::float32_type.
 * @param obj Python object (number-like).
 * @param out Output reference to store the converted value.
 * @return 0 on success, -1 on failure (with Python exception set).
 */
int py_to_f32(PyObject* obj, ::auto_th10::float32_type& out) noexcept {
    if (obj == nullptr) {
        PyErr_SetString(PyExc_TypeError, "expected a number, got None");
        return -1;
    }
    PyObject* f = PyNumber_Float(obj);
    if (f == nullptr) {
        PyErr_SetString(PyExc_TypeError, "expected a number");
        return -1;
    }
    const double v = PyFloat_AsDouble(f);
    Py_DECREF(f);
    if (PyErr_Occurred()) {
        return -1;
    }
    out = static_cast<::auto_th10::float32_type>(v);
    return 0;
}

/** @brief Convert float32_type to Python float. */
PyObject* f32_to_py(::auto_th10::float32_type v) noexcept {
    return PyFloat_FromDouble(static_cast<double>(v));
}
} // anonymous namespace

/* ===== Base wrapper: ThObject ===== */

struct PyThObject {
    PyObject_HEAD ::auto_th10::ThObject* cpp_obj;
};

namespace {
/** @brief Deallocate PyThObject. */
void PyThObject_dealloc(PyThObject* self) noexcept {
    delete self->cpp_obj;
    self->cpp_obj = nullptr;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
}
} // anonymous namespace

namespace {
/** @brief __new__(x, y) for ThObject. */
PyObject* PyThObject_new(PyTypeObject* type, PyObject* args, PyObject* kwds) noexcept {
    static const char* kwlist[] = {"x", "y", nullptr};
    PyObject *xobj = nullptr, *yobj = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO", const_cast<char**>(kwlist), &xobj, &yobj)) {
        return nullptr;
    }
    ::auto_th10::float32_type x, y;
    if (py_to_f32(xobj, x) != 0)
        return nullptr;
    if (py_to_f32(yobj, y) != 0)
        return nullptr;

    PyThObject* self = reinterpret_cast<PyThObject*>(type->tp_alloc(type, 0));
    if (self == nullptr)
        return nullptr;

    self->cpp_obj = new (std::nothrow)::auto_th10::ThObject(x, y);
    if (self->cpp_obj == nullptr) {
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        PyErr_NoMemory();
        return nullptr;
    }
    return reinterpret_cast<PyObject*>(self);
}
} // anonymous namespace

namespace {
/** @brief Getter for x. */
PyObject* ThObject_get_x(PyThObject* self, void*) noexcept {
    return f32_to_py(self->cpp_obj->x);
}
} // anonymous namespace

namespace {
/** @brief Setter for x. */
int ThObject_set_x(PyThObject* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'x'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_obj->x = v;
    return 0;
}
} // anonymous namespace

namespace {
/** @brief Getter for y. */
PyObject* ThObject_get_y(PyThObject* self, void*) noexcept {
    return f32_to_py(self->cpp_obj->y);
}
} // anonymous namespace

namespace {
/** @brief Setter for y. */
int ThObject_set_y(PyThObject* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'y'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_obj->y = v;
    return 0;
}
} // anonymous namespace

static PyGetSetDef ThObject_getset[] = {
    {"x", reinterpret_cast<getter>(ThObject_get_x), reinterpret_cast<setter>(ThObject_set_x),
     const_cast<char*>("x coordinate"), nullptr},
    {"y", reinterpret_cast<getter>(ThObject_get_y), reinterpret_cast<setter>(ThObject_set_y),
     const_cast<char*>("y coordinate"), nullptr},
    {nullptr, nullptr, nullptr, nullptr, nullptr}};

static PyTypeObject PyThObjectType = {
    PyVarObject_HEAD_INIT(nullptr, 0).tp_name = "auto_th10.ThObject",
    .tp_basicsize = sizeof(PyThObject),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = const_cast<char*>("ThObject base type"),
    .tp_new = PyThObject_new,
    .tp_dealloc = reinterpret_cast<destructor>(PyThObject_dealloc),
    .tp_getset = ThObject_getset,
};

/* ===== Derived wrappers (embed PyThObject as first field to match inheritance) ===== */

/* --- Player : ThObject --- */
struct PyPlayer {
    PyThObject base; /**< must be first for binary compatibility with base getsets */
    ::auto_th10::Player* cpp_player; /**< convenience pointer to derived */
};

namespace {
void PyPlayer_dealloc(PyPlayer* self) noexcept {
    delete self->cpp_player;
    self->cpp_player = nullptr;
    self->base.cpp_obj = nullptr;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
}
} // anonymous namespace

namespace {
PyObject* PyPlayer_new(PyTypeObject* type, PyObject* args, PyObject* kwds) noexcept {
    static const char* kwlist[] = {"x", "y", nullptr};
    PyObject *xobj = nullptr, *yobj = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO", const_cast<char**>(kwlist), &xobj, &yobj)) {
        return nullptr;
    }
    ::auto_th10::float32_type x, y;
    if (py_to_f32(xobj, x) != 0)
        return nullptr;
    if (py_to_f32(yobj, y) != 0)
        return nullptr;

    PyPlayer* self = reinterpret_cast<PyPlayer*>(type->tp_alloc(type, 0));
    if (self == nullptr)
        return nullptr;

    self->cpp_player = new (std::nothrow)::auto_th10::Player(x, y);
    if (self->cpp_player == nullptr) {
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        PyErr_NoMemory();
        return nullptr;
    }
    self->base.cpp_obj = static_cast<::auto_th10::ThObject*>(self->cpp_player);
    return reinterpret_cast<PyObject*>(self);
}
} // anonymous namespace

static PyTypeObject PyPlayerType = {
    PyVarObject_HEAD_INIT(nullptr, 0).tp_name = "auto_th10.Player",
    .tp_basicsize = sizeof(PyPlayer),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = const_cast<char*>("Player (inherits ThObject)"),
    .tp_new = PyPlayer_new,
    .tp_dealloc = reinterpret_cast<destructor>(PyPlayer_dealloc),
    .tp_base = &PyThObjectType,
};

/* --- Enemy : ThObject --- */
struct PyEnemy {
    PyThObject base;
    ::auto_th10::Enemy* cpp_enemy;
};

namespace {
void PyEnemy_dealloc(PyEnemy* self) noexcept {
    delete self->cpp_enemy;
    self->cpp_enemy = nullptr;
    self->base.cpp_obj = nullptr;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
}

PyObject* PyEnemy_new(PyTypeObject* type, PyObject* args, PyObject* kwds) noexcept {
    static const char* kwlist[] = {"x", "y", "width", "height", nullptr};
    PyObject *xobj = nullptr, *yobj = nullptr, *wobj = nullptr, *hobj = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOOO", const_cast<char**>(kwlist), &xobj, &yobj, &wobj, &hobj)) {
        return nullptr;
    }
    ::auto_th10::float32_type x, y, w, h;
    if (py_to_f32(xobj, x) != 0)
        return nullptr;
    if (py_to_f32(yobj, y) != 0)
        return nullptr;
    if (py_to_f32(wobj, w) != 0)
        return nullptr;
    if (py_to_f32(hobj, h) != 0)
        return nullptr;

    PyEnemy* self = reinterpret_cast<PyEnemy*>(type->tp_alloc(type, 0));
    if (self == nullptr)
        return nullptr;

    self->cpp_enemy = new (std::nothrow)::auto_th10::Enemy(x, y, w, h);
    if (self->cpp_enemy == nullptr) {
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        PyErr_NoMemory();
        return nullptr;
    }
    self->base.cpp_obj = static_cast<::auto_th10::ThObject*>(self->cpp_enemy);
    return reinterpret_cast<PyObject*>(self);
}

/* Enemy width/height properties */
PyObject* Enemy_get_width(PyEnemy* self, void*) noexcept {
    return f32_to_py(self->cpp_enemy->width);
}

int Enemy_set_width(PyEnemy* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'width'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_enemy->width = v;
    return 0;
}

static PyObject* Enemy_get_height(PyEnemy* self, void*) noexcept {
    return f32_to_py(self->cpp_enemy->height);
}

static int Enemy_set_height(PyEnemy* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'height'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_enemy->height = v;
    return 0;
}
} // anonymous namespace

static PyGetSetDef Enemy_getset[] = {{"width", reinterpret_cast<getter>(Enemy_get_width),
                                      reinterpret_cast<setter>(Enemy_set_width), const_cast<char*>("width"), nullptr},
                                     {"height", reinterpret_cast<getter>(Enemy_get_height),
                                      reinterpret_cast<setter>(Enemy_set_height), const_cast<char*>("height"), nullptr},
                                     {nullptr, nullptr, nullptr, nullptr, nullptr}};

static PyTypeObject PyEnemyType = {
    PyVarObject_HEAD_INIT(nullptr, 0).tp_name = "auto_th10.Enemy",
    .tp_basicsize = sizeof(PyEnemy),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = const_cast<char*>("Enemy (inherits ThObject)"),
    .tp_new = PyEnemy_new,
    .tp_dealloc = reinterpret_cast<destructor>(PyEnemy_dealloc),
    .tp_getset = Enemy_getset,
    .tp_base = &PyThObjectType,
};

/* --- EnemyBullet : ThObject --- */
struct PyEnemyBullet {
    PyThObject base;
    ::auto_th10::EnemyBullet* cpp_bullet;
};

namespace {
void PyEnemyBullet_dealloc(PyEnemyBullet* self) noexcept {
    delete self->cpp_bullet;
    self->cpp_bullet = nullptr;
    self->base.cpp_obj = nullptr;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
}
} // anonymous namespace

namespace {
PyObject* PyEnemyBullet_new(PyTypeObject* type, PyObject* args, PyObject* kwds) noexcept {
    static const char* kwlist[] = {"x", "y", "width", "height", "dx", "dy", nullptr};
    PyObject *xobj = nullptr, *yobj = nullptr, *wobj = nullptr, *hobj = nullptr, *dxobj = nullptr, *dyobj = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOOOOO", const_cast<char**>(kwlist), &xobj, &yobj, &wobj, &hobj,
                                     &dxobj, &dyobj)) {
        return nullptr;
    }
    ::auto_th10::float32_type x, y, w, h, dx, dy;
    if (py_to_f32(xobj, x) != 0)
        return nullptr;
    if (py_to_f32(yobj, y) != 0)
        return nullptr;
    if (py_to_f32(wobj, w) != 0)
        return nullptr;
    if (py_to_f32(hobj, h) != 0)
        return nullptr;
    if (py_to_f32(dxobj, dx) != 0)
        return nullptr;
    if (py_to_f32(dyobj, dy) != 0)
        return nullptr;

    PyEnemyBullet* self = reinterpret_cast<PyEnemyBullet*>(type->tp_alloc(type, 0));
    if (self == nullptr)
        return nullptr;

    self->cpp_bullet = new (std::nothrow)::auto_th10::EnemyBullet(x, y, w, h, dx, dy);
    if (self->cpp_bullet == nullptr) {
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        PyErr_NoMemory();
        return nullptr;
    }
    self->base.cpp_obj = static_cast<::auto_th10::ThObject*>(self->cpp_bullet);
    return reinterpret_cast<PyObject*>(self);
}
} // anonymous namespace

/* EnemyBullet extra properties */
namespace {
PyObject* Bullet_get_width(PyEnemyBullet* self, void*) noexcept {
    return f32_to_py(self->cpp_bullet->width);
}
} // anonymous namespace

namespace {
int Bullet_set_width(PyEnemyBullet* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'width'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_bullet->width = v;
    return 0;
}

PyObject* Bullet_get_height(PyEnemyBullet* self, void*) noexcept {
    return f32_to_py(self->cpp_bullet->height);
}

int Bullet_set_height(PyEnemyBullet* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'height'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_bullet->height = v;
    return 0;
}

PyObject* Bullet_get_dx(PyEnemyBullet* self, void*) noexcept {
    return f32_to_py(self->cpp_bullet->dx);
}

int Bullet_set_dx(PyEnemyBullet* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'dx'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_bullet->dx = v;
    return 0;
}

PyObject* Bullet_get_dy(PyEnemyBullet* self, void*) noexcept {
    return f32_to_py(self->cpp_bullet->dy);
}

int Bullet_set_dy(PyEnemyBullet* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'dy'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_bullet->dy = v;
    return 0;
}
} // anonymous namespace

static PyGetSetDef EnemyBullet_getset[] = {
    {"width", reinterpret_cast<getter>(Bullet_get_width), reinterpret_cast<setter>(Bullet_set_width),
     const_cast<char*>("width"), nullptr},
    {"height", reinterpret_cast<getter>(Bullet_get_height), reinterpret_cast<setter>(Bullet_set_height),
     const_cast<char*>("height"), nullptr},
    {"dx", reinterpret_cast<getter>(Bullet_get_dx), reinterpret_cast<setter>(Bullet_set_dx), const_cast<char*>("dx"),
     nullptr},
    {"dy", reinterpret_cast<getter>(Bullet_get_dy), reinterpret_cast<setter>(Bullet_set_dy), const_cast<char*>("dy"),
     nullptr},
    {nullptr, nullptr, nullptr, nullptr, nullptr}};

static PyTypeObject PyEnemyBulletType = {
    PyVarObject_HEAD_INIT(nullptr, 0).tp_name = "auto_th10.EnemyBullet",
    .tp_basicsize = sizeof(PyEnemyBullet),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = const_cast<char*>("EnemyBullet (inherits ThObject)"),
    .tp_new = PyEnemyBullet_new,
    .tp_dealloc = reinterpret_cast<destructor>(PyEnemyBullet_dealloc),
    .tp_getset = EnemyBullet_getset,
    .tp_base = &PyThObjectType,
};

/* --- EnemyLaser : ThObject --- */
struct PyEnemyLaser {
    PyThObject base;
    ::auto_th10::EnemyLaser* cpp_laser;
};

namespace {
void PyEnemyLaser_dealloc(PyEnemyLaser* self) noexcept {
    delete self->cpp_laser;
    self->cpp_laser = nullptr;
    self->base.cpp_obj = nullptr;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
}

PyObject* PyEnemyLaser_new(PyTypeObject* type, PyObject* args, PyObject* kwds) noexcept {
    static const char* kwlist[] = {"x", "y", "width", "height", "radian", nullptr};
    PyObject *xobj = nullptr, *yobj = nullptr, *wobj = nullptr, *hobj = nullptr, *robj = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOOOO", const_cast<char**>(kwlist), &xobj, &yobj, &wobj, &hobj,
                                     &robj)) {
        return nullptr;
    }
    ::auto_th10::float32_type x, y, w, h, r;
    if (py_to_f32(xobj, x) != 0)
        return nullptr;
    if (py_to_f32(yobj, y) != 0)
        return nullptr;
    if (py_to_f32(wobj, w) != 0)
        return nullptr;
    if (py_to_f32(hobj, h) != 0)
        return nullptr;
    if (py_to_f32(robj, r) != 0)
        return nullptr;

    PyEnemyLaser* self = reinterpret_cast<PyEnemyLaser*>(type->tp_alloc(type, 0));
    if (self == nullptr)
        return nullptr;

    self->cpp_laser = new (std::nothrow)::auto_th10::EnemyLaser(x, y, w, h, r);
    if (self->cpp_laser == nullptr) {
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        PyErr_NoMemory();
        return nullptr;
    }
    self->base.cpp_obj = static_cast<::auto_th10::ThObject*>(self->cpp_laser);
    return reinterpret_cast<PyObject*>(self);
}
} // anonymous namespace

/* EnemyLaser properties: width/height/radian */
namespace {
PyObject* Laser_get_width(PyEnemyLaser* self, void*) noexcept {
    return f32_to_py(self->cpp_laser->width);
}

int Laser_set_width(PyEnemyLaser* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'width'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_laser->width = v;
    return 0;
}

PyObject* Laser_get_height(PyEnemyLaser* self, void*) noexcept {
    return f32_to_py(self->cpp_laser->height);
}

int Laser_set_height(PyEnemyLaser* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'height'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_laser->height = v;
    return 0;
}

PyObject* Laser_get_radian(PyEnemyLaser* self, void*) noexcept {
    return f32_to_py(self->cpp_laser->radian);
}

int Laser_set_radian(PyEnemyLaser* self, PyObject* value, void*) noexcept {
    if (value == nullptr) {
        PyErr_SetString(PyExc_AttributeError, "cannot delete attribute 'radian'");
        return -1;
    }
    ::auto_th10::float32_type v;
    if (py_to_f32(value, v) != 0)
        return -1;
    self->cpp_laser->radian = v;
    return 0;
}
} // anonymous namespace

static PyGetSetDef EnemyLaser_getset[] = {
    {"width", reinterpret_cast<getter>(Laser_get_width), reinterpret_cast<setter>(Laser_set_width),
     const_cast<char*>("width"), nullptr},
    {"height", reinterpret_cast<getter>(Laser_get_height), reinterpret_cast<setter>(Laser_set_height),
     const_cast<char*>("height"), nullptr},
    {"radian", reinterpret_cast<getter>(Laser_get_radian), reinterpret_cast<setter>(Laser_set_radian),
     const_cast<char*>("angle in radians"), nullptr},
    {nullptr, nullptr, nullptr, nullptr, nullptr}};

static PyTypeObject PyEnemyLaserType = {
    PyVarObject_HEAD_INIT(nullptr, 0).tp_name = "auto_th10.EnemyLaser",
    .tp_basicsize = sizeof(PyEnemyLaser),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = const_cast<char*>("EnemyLaser (inherits ThObject)"),
    .tp_new = PyEnemyLaser_new,
    .tp_dealloc = reinterpret_cast<destructor>(PyEnemyLaser_dealloc),
    .tp_getset = EnemyLaser_getset,
    .tp_base = &PyThObjectType,
};

/* --- Resource : ThObject --- */
struct PyResource {
    PyThObject base;
    ::auto_th10::Resource* cpp_res;
};

namespace {
void PyResource_dealloc(PyResource* self) noexcept {
    delete self->cpp_res;
    self->cpp_res = nullptr;
    self->base.cpp_obj = nullptr;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
}

PyObject* PyResource_new(PyTypeObject* type, PyObject* args, PyObject* kwds) noexcept {
    static const char* kwlist[] = {"x", "y", nullptr};
    PyObject *xobj = nullptr, *yobj = nullptr;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO", const_cast<char**>(kwlist), &xobj, &yobj)) {
        return nullptr;
    }
    ::auto_th10::float32_type x, y;
    if (py_to_f32(xobj, x) != 0)
        return nullptr;
    if (py_to_f32(yobj, y) != 0)
        return nullptr;

    PyResource* self = reinterpret_cast<PyResource*>(type->tp_alloc(type, 0));
    if (self == nullptr)
        return nullptr;

    self->cpp_res = new (std::nothrow)::auto_th10::Resource(x, y);
    if (self->cpp_res == nullptr) {
        Py_TYPE(self)->tp_free(reinterpret_cast<PyObject*>(self));
        PyErr_NoMemory();
        return nullptr;
    }
    self->base.cpp_obj = static_cast<::auto_th10::ThObject*>(self->cpp_res);
    return reinterpret_cast<PyObject*>(self);
}
} // anonymous namespace

static PyTypeObject PyResourceType = {
    PyVarObject_HEAD_INIT(nullptr, 0).tp_name = "auto_th10.Resource",
    .tp_basicsize = sizeof(PyResource),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = const_cast<char*>("Resource (inherits ThObject)"),
    .tp_new = PyResource_new,
    .tp_dealloc = reinterpret_cast<destructor>(PyResource_dealloc),
    .tp_base = &PyThObjectType,
};

static PyObject* get_player(PyObject*, PyObject* args) noexcept {
    unsigned long long handle;
    if (!PyArg_ParseTuple(args, "K", &handle)) {
        return nullptr;
    }
    auto player = ::auto_th10::get_player(reinterpret_cast<HANDLE>(handle));
    PyObject* mod = PyImport_ImportModule("auto_th10");
    if (!mod) return nullptr;
    PyObject* cls = PyObject_GetAttrString(mod, "Player");
    Py_DECREF(mod);
    if (!cls) return nullptr;
    PyObject* obj = PyObject_CallFunction(cls, "ff", player.x, player.y);
    Py_DECREF(cls);
    return obj;
}

static PyObject* get_enemies(PyObject*, PyObject* args) noexcept {
    unsigned long long handle;
    if (!PyArg_ParseTuple(args, "K", &handle)) {
        return nullptr;
    }
    auto enemies = ::auto_th10::get_enemies(reinterpret_cast<HANDLE>(handle));
    PyObject* list = PyList_New(enemies.size());
    if (!list) return nullptr;
    PyObject* mod = PyImport_ImportModule("auto_th10");
    if (!mod) return nullptr;
    PyObject* cls = PyObject_GetAttrString(mod, "Enemy");
    Py_DECREF(mod);
    if (!cls) return nullptr;
    for (size_t i = 0; i < enemies.size(); i++) {
        auto const& e = enemies[i];
        PyObject* obj = PyObject_CallFunction(cls, "ffff", e.x, e.y, e.width, e.height);
        PyList_SET_ITEM(list, i, obj); // steals ref
    }
    Py_DECREF(cls);
    return list;
}

static PyObject* get_enemy_bullets(PyObject*, PyObject* args) noexcept {
    unsigned long long handle;
    if (!PyArg_ParseTuple(args, "K", &handle)) {
        return nullptr;
    }
    auto bullets = ::auto_th10::get_enemy_bullets(reinterpret_cast<HANDLE>(handle));
    PyObject* list = PyList_New(bullets.size());
    if (!list) return nullptr;
    PyObject* mod = PyImport_ImportModule("auto_th10");
    if (!mod) return nullptr;
    PyObject* cls = PyObject_GetAttrString(mod, "EnemyBullet");
    Py_DECREF(mod);
    if (!cls) return nullptr;
    for (size_t i = 0; i < bullets.size(); i++) {
        auto const& b = bullets[i];
        PyObject* obj = PyObject_CallFunction(cls, "ffffff", b.x, b.y, b.width, b.height, b.dx, b.dy);
        PyList_SET_ITEM(list, i, obj);
    }
    Py_DECREF(cls);
    return list;
}

static PyObject* get_enemy_lasers(PyObject*, PyObject* args) noexcept {
    unsigned long long handle;
    if (!PyArg_ParseTuple(args, "K", &handle)) {
        return nullptr;
    }
    auto lasers = ::auto_th10::get_enemy_lasers(reinterpret_cast<HANDLE>(handle));
    PyObject* list = PyList_New(lasers.size());
    if (!list) return nullptr;
    PyObject* mod = PyImport_ImportModule("auto_th10");
    if (!mod) return nullptr;
    PyObject* cls = PyObject_GetAttrString(mod, "EnemyLaser");
    Py_DECREF(mod);
    if (!cls) return nullptr;
    for (size_t i = 0; i < lasers.size(); i++) {
        auto const& l = lasers[i];
        PyObject* obj = PyObject_CallFunction(cls, "fffff", l.x, l.y, l.width, l.height, l.radian);
        PyList_SET_ITEM(list, i, obj);
    }
    Py_DECREF(cls);
    return list;
}

static PyObject* get_resources(PyObject*, PyObject* args) noexcept {
    unsigned long long handle;
    if (!PyArg_ParseTuple(args, "K", &handle)) {
        return nullptr;
    }
    auto res = ::auto_th10::get_resources(reinterpret_cast<HANDLE>(handle));
    PyObject* list = PyList_New(res.size());
    if (!list) return nullptr;
    PyObject* mod = PyImport_ImportModule("auto_th10");
    if (!mod) return nullptr;
    PyObject* cls = PyObject_GetAttrString(mod, "Resource");
    Py_DECREF(mod);
    if (!cls) return nullptr;
    for (size_t i = 0; i < res.size(); i++) {
        auto const& r = res[i];
        PyObject* obj = PyObject_CallFunction(cls, "ff", r.x, r.y);
        PyList_SET_ITEM(list, i, obj);
    }
    Py_DECREF(cls);
    return list;
}

static PyObject* get_score(PyObject*, PyObject* args) noexcept {
    unsigned long long handle;
    if (!PyArg_ParseTuple(args, "K", &handle)) {
        return nullptr;
    }
    auto score = ::auto_th10::get_score(reinterpret_cast<HANDLE>(handle));
    return PyLong_FromUnsignedLong(score);
}

static PyObject* get_power(PyObject*, PyObject* args) noexcept {
    unsigned long long handle;
    if (!PyArg_ParseTuple(args, "K", &handle)) {
        return nullptr;
    }
    auto power = ::auto_th10::get_power(reinterpret_cast<HANDLE>(handle));
    return PyLong_FromUnsignedLong(power);
}

static PyObject* get_hp(PyObject*, PyObject* args) noexcept {
    unsigned long long handle;
    if (!PyArg_ParseTuple(args, "K", &handle)) {
        return nullptr;
    }
    auto hp = ::auto_th10::get_hp(reinterpret_cast<HANDLE>(handle));
    return PyLong_FromLong(hp);
}

/* ===== Module boilerplate ===== */

static PyMethodDef auto_th10_methods[] = {
    {"get_hwnd", (PyCFunction)get_hwnd, METH_NOARGS, "Get TH10 window handle (HWND as int)"},
    {"get_pid_and_tid", (PyCFunction)get_pid_and_tid, METH_VARARGS, "Get process id and thread id from HWND"},
    {"get_process_handle", (PyCFunction)get_process_handle, METH_VARARGS, "Get process handle from PID"},
    {"is_game_over", (PyCFunction)is_game_over, METH_VARARGS, "Check if the game is over (hp == -1)"},
    {"set_as_foreground", (PyCFunction)set_as_foreground, METH_VARARGS, "Set the game window as foreground"},
    {"get_player", get_player, METH_VARARGS, "Get player object"},
    {"get_enemies", get_enemies, METH_VARARGS, "Get enemies list"},
    {"get_enemy_bullets", get_enemy_bullets, METH_VARARGS, "Get enemy bullets list"},
    {"get_enemy_lasers", get_enemy_lasers, METH_VARARGS, "Get enemy lasers list"},
    {"get_resources", get_resources, METH_VARARGS, "Get resources list"},
    {"get_score", get_score, METH_VARARGS, "Get current score"},
    {"get_power", get_power, METH_VARARGS, "Get current power"},
    {"get_hp", get_hp, METH_VARARGS, "Get player hp"},
    {nullptr, nullptr, 0, nullptr}};


static struct PyModuleDef auto_th10_module = {PyModuleDef_HEAD_INIT,
                                              "auto_th10",
                                              "Python bindings for auto_th10 classes.",
                                              -1,
                                              auto_th10_methods,
                                              nullptr,
                                              nullptr,
                                              nullptr,
                                              nullptr};

/**
 * @brief Initialize the module and register all types.
 */
PyMODINIT_FUNC PyInit_auto_th10(void) noexcept {
    if (PyType_Ready(&PyThObjectType) < 0)
        return nullptr;
    if (PyType_Ready(&PyPlayerType) < 0)
        return nullptr;
    if (PyType_Ready(&PyEnemyType) < 0)
        return nullptr;
    if (PyType_Ready(&PyEnemyBulletType) < 0)
        return nullptr;
    if (PyType_Ready(&PyEnemyLaserType) < 0)
        return nullptr;
    if (PyType_Ready(&PyResourceType) < 0)
        return nullptr;

    PyObject* m = PyModule_Create(&auto_th10_module);
    if (m == nullptr)
        return nullptr;

    /* Add types */
    Py_INCREF(&PyThObjectType);
    if (PyModule_AddObject(m, "ThObject", reinterpret_cast<PyObject*>(&PyThObjectType)) < 0) {
        Py_DECREF(&PyThObjectType);
        Py_DECREF(m);
        return nullptr;
    }

    Py_INCREF(&PyPlayerType);
    if (PyModule_AddObject(m, "Player", reinterpret_cast<PyObject*>(&PyPlayerType)) < 0) {
        Py_DECREF(&PyPlayerType);
        Py_DECREF(m);
        return nullptr;
    }

    Py_INCREF(&PyEnemyType);
    if (PyModule_AddObject(m, "Enemy", reinterpret_cast<PyObject*>(&PyEnemyType)) < 0) {
        Py_DECREF(&PyEnemyType);
        Py_DECREF(m);
        return nullptr;
    }

    Py_INCREF(&PyEnemyBulletType);
    if (PyModule_AddObject(m, "EnemyBullet", reinterpret_cast<PyObject*>(&PyEnemyBulletType)) < 0) {
        Py_DECREF(&PyEnemyBulletType);
        Py_DECREF(m);
        return nullptr;
    }

    Py_INCREF(&PyEnemyLaserType);
    if (PyModule_AddObject(m, "EnemyLaser", reinterpret_cast<PyObject*>(&PyEnemyLaserType)) < 0) {
        Py_DECREF(&PyEnemyLaserType);
        Py_DECREF(m);
        return nullptr;
    }

    Py_INCREF(&PyResourceType);
    if (PyModule_AddObject(m, "Resource", reinterpret_cast<PyObject*>(&PyResourceType)) < 0) {
        Py_DECREF(&PyResourceType);
        Py_DECREF(m);
        return nullptr;
    }

    /* Add static constexpr class attributes for Player and Resource */
    {
        PyObject* pw = f32_to_py(::auto_th10::Player::width);
        PyObject* ph = f32_to_py(::auto_th10::Player::height);
        if (pw == nullptr || ph == nullptr) {
            Py_XDECREF(pw);
            Py_XDECREF(ph);
            Py_DECREF(m);
            return nullptr;
        }
        if (PyDict_SetItemString(PyPlayerType.tp_dict, "width", pw) < 0 ||
            PyDict_SetItemString(PyPlayerType.tp_dict, "height", ph) < 0) {
            Py_DECREF(pw);
            Py_DECREF(ph);
            Py_DECREF(m);
            return nullptr;
        }
        Py_DECREF(pw);
        Py_DECREF(ph);

        PyObject* rw = f32_to_py(::auto_th10::Resource::width);
        PyObject* rh = f32_to_py(::auto_th10::Resource::height);
        if (rw == nullptr || rh == nullptr) {
            Py_XDECREF(rw);
            Py_XDECREF(rh);
            Py_DECREF(m);
            return nullptr;
        }
        if (PyDict_SetItemString(PyResourceType.tp_dict, "width", rw) < 0 ||
            PyDict_SetItemString(PyResourceType.tp_dict, "height", rh) < 0) {
            Py_DECREF(rw);
            Py_DECREF(rh);
            Py_DECREF(m);
            return nullptr;
        }
        Py_DECREF(rw);
        Py_DECREF(rh);
    }

    return m;
}
