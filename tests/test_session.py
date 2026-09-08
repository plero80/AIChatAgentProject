from Session import resolve_session_id


def test_missing_cookie_issues_a_new_uuid():
    user_id, is_new = resolve_session_id(None)
    assert is_new is True
    again, still_new = resolve_session_id(user_id)
    assert still_new is False
    assert again == user_id


def test_client_forged_ids_are_rejected():
    user_id, is_new = resolve_session_id("web-user")
    assert is_new is True
    assert user_id != "web-user"


def test_attacker_cannot_steal_a_thread_by_guessing_garbage():
    stolen, is_new = resolve_session_id("persist-test-user")
    assert is_new is True
    assert stolen != "persist-test-user"
