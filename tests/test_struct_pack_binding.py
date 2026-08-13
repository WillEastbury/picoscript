from picoscript_cfront import Parser, tokenize
from picostore import PicoStore


def test_struct_binds_to_pack_schema_deterministically():
    program = Parser(tokenize("""
struct User {
    @id(0) int id;
    @id(1) text[40] name;
    @id(2) int flags;
};
""")).parse_program()
    store = PicoStore()
    first = store.bind_struct("users", program[0])
    second = store.bind_struct("users", program[0])
    assert first == second
    assert first["version"] == 1
    assert [field["id"] for field in first["schema"]["fields"]] == [0, 1, 2]
