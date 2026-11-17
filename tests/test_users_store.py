import errno
import json
import threading
from unittest import mock

import pytest

from services import users_store


def test_guardado_normal(tmp_path):
    p = tmp_path / 'users.json'
    users_store.save_users([{'username': 'a'}], str(p))
    assert json.loads(p.read_text(encoding='utf-8')) == [{'username': 'a'}]


def test_ebusy_escribe_en_sitio(tmp_path):
    p = tmp_path / 'users.json'
    p.write_text('[]', encoding='utf-8')

    def busy(src, dst):
        raise OSError(errno.EBUSY, 'Device or resource busy')

    with mock.patch('services.users_store.os.replace', busy):
        users_store.save_users([{'username': 'b'}], str(p))
    assert json.loads(p.read_text(encoding='utf-8')) == [{'username': 'b'}]
    assert (tmp_path / 'users.json.bak').exists()
    assert not list(tmp_path.glob('users.json.tmp.*'))


def test_otro_error_se_propaga(tmp_path):
    p = tmp_path / 'users.json'
    p.write_text('[]', encoding='utf-8')

    def denied(src, dst):
        raise OSError(errno.EACCES, 'denied')

    with mock.patch('services.users_store.os.replace', denied):
        with pytest.raises(OSError):
            users_store.save_users([{'username': 'c'}], str(p))
    assert json.loads(p.read_text(encoding='utf-8')) == []
    assert not list(tmp_path.glob('users.json.tmp.*'))


def test_escrituras_concurrentes(tmp_path):
    p = tmp_path / 'users.json'
    errores = []

    def w(i):
        try:
            users_store.save_users([{'username': 'u%d' % i}], str(p))
        except Exception as e:
            errores.append(e)

    hilos = [threading.Thread(target=w, args=(i,)) for i in range(20)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert not errores
    data = json.loads(p.read_text(encoding='utf-8'))
    assert isinstance(data, list) and len(data) == 1


def test_lectura_reintenta_si_json_parcial(tmp_path):
    p = tmp_path / 'users.json'
    p.write_text('[{"user', encoding='utf-8')

    def arreglar(_):
        p.write_text('[{"username": "z"}]', encoding='utf-8')

    with mock.patch('services.users_store.time.sleep', arreglar):
        assert users_store.load_users(str(p)) == [{'username': 'z'}]


def test_lectura_json_corrupto_persistente(tmp_path):
    p = tmp_path / 'users.json'
    p.write_text('[{"user', encoding='utf-8')
    with mock.patch('services.users_store.time.sleep', lambda _: None):
        with pytest.raises(ValueError):
            users_store.load_users(str(p))


def test_lectura_archivo_inexistente(tmp_path):
    assert users_store.load_users(str(tmp_path / 'nope.json')) == []
