from services.ast_service import extract_functions, extract_classes, JS_LANGUAGE


PY_CODE = """
import os

def hello():
    print("hello")

class Foo:
    def bar(self, x):
        return x + 1

def add(a, b):
    return a + b
"""

JS_CODE = """
function greet(name) {
    return "hello " + name;
}

const add = (a, b) => a + b;

class MyClass {
    method1() {
        return 1;
    }

    method2(x) {
        return x * 2;
    }
}
"""


def test_extract_functions():
    funcs = extract_functions(PY_CODE)
    names = [f["name"] for f in funcs]
    assert "hello" in names
    assert "bar" in names
    assert "add" in names
    assert len(funcs) == 3


def test_extract_function_body():
    funcs = extract_functions(PY_CODE)
    hello = [f for f in funcs if f["name"] == "hello"][0]
    assert hello["start_line"] == 4
    assert hello["body"].startswith("def hello")


def test_empty_code():
    funcs = extract_functions("")
    assert funcs == []


def test_code_without_functions():
    funcs = extract_functions("x = 1\ny = 2\n")
    assert funcs == []


def test_extract_js_functions():
    funcs = extract_functions(JS_CODE, JS_LANGUAGE)
    names = [f["name"] for f in funcs]
    assert "greet" in names
    assert "method1" in names
    assert "method2" in names


def test_extract_js_function_body():
    funcs = extract_functions(JS_CODE, JS_LANGUAGE)
    greet = [f for f in funcs if f["name"] == "greet"][0]
    assert greet["body"].startswith("function greet")


def test_extract_classes_python():
    classes = extract_classes(PY_CODE)
    names = [k["name"] for k in classes]
    assert "Foo" in names
    assert len(classes) == 1


def test_extract_class_with_methods():
    classes = extract_classes(PY_CODE)
    foo = [k for k in classes if k["name"] == "Foo"][0]
    method_names = [m["name"] for m in foo["methods"]]
    assert "bar" in method_names
    assert foo["start_line"] > 0
    assert "class Foo" in foo["body"]


def test_extract_classes_empty():
    assert extract_classes("") == []


def test_extract_classes_js():
    classes = extract_classes(JS_CODE, JS_LANGUAGE)
    names = [k["name"] for k in classes]
    assert "MyClass" in names
    my = [k for k in classes if k["name"] == "MyClass"][0]
    method_names = [m["name"] for m in my["methods"]]
    assert "method1" in method_names
    assert "method2" in method_names
