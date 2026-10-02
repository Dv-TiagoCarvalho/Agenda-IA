from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import api

cliente = TestClient(api.app)
contador = iter(range(1, 10_000))


def proxima_segunda():
    dia = date.today() + timedelta(days=1)
    while dia.weekday() != 0:
        dia += timedelta(days=1)
    return dia


def novo_paciente():
    """Cria um usuário novo e devolve o cabeçalho com o token dele."""
    email = f"paciente{next(contador)}@teste.com"
    cliente.post("/usuarios", json={"nome": "Paciente", "email": email, "senha": "segredo1"})
    token = cliente.post("/login", json={"email": email, "senha": "segredo1"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


@pytest.fixture
def paciente():
    return novo_paciente()


@pytest.fixture
def outro_paciente():
    return novo_paciente()


@pytest.fixture
def horario_livre(paciente):
    """Um horário livre da Dra. Ana Lima (médico 1) na próxima segunda."""
    horarios = cliente.get(
        f"/medicos/1/horarios?data={proxima_segunda()}", headers=paciente
    ).json()
    return horarios[0]


# ------------------------------------------------------------------- usuários


def test_cadastro_nao_devolve_a_senha():
    resposta = cliente.post(
        "/usuarios", json={"nome": "Ana", "email": "Ana@Teste.com", "senha": "segredo1"}
    )
    assert resposta.status_code == 201
    assert resposta.json()["email"] == "ana@teste.com"
    assert "senha" not in resposta.text


def test_email_repetido_e_recusado():
    dados = {"nome": "Bia", "email": "bia@teste.com", "senha": "segredo1"}
    assert cliente.post("/usuarios", json=dados).status_code == 201
    assert cliente.post("/usuarios", json=dados).status_code == 409


def test_senha_curta_e_recusada():
    dados = {"nome": "Caio", "email": "caio@teste.com", "senha": "123"}
    assert cliente.post("/usuarios", json=dados).status_code == 422


def test_login_com_senha_errada():
    cliente.post("/usuarios", json={"nome": "Davi", "email": "davi@teste.com", "senha": "segredo1"})
    resposta = cliente.post("/login", json={"email": "davi@teste.com", "senha": "errada"})
    assert resposta.status_code == 401


def test_rotas_protegidas_exigem_token(paciente):
    assert cliente.get("/medicos").status_code == 401
    assert cliente.get("/medicos", headers={"Authorization": "Bearer invalido"}).status_code == 401
    assert cliente.get("/medicos", headers=paciente).status_code == 200


# -------------------------------------------------------------------- agenda


def test_horarios_seguem_a_disponibilidade_do_medico(paciente):
    segunda = proxima_segunda()
    horarios = cliente.get(f"/medicos/1/horarios?data={segunda}", headers=paciente).json()
    assert horarios, "a Dra. Ana Lima atende segunda de manhã"
    assert all("08:00" <= horario[11:16] <= "11:30" for horario in horarios)

    domingo = segunda - timedelta(days=1)
    assert cliente.get(f"/medicos/1/horarios?data={domingo}", headers=paciente).json() == []


def test_marcar_consulta_tira_o_horario_da_lista(paciente, horario_livre):
    resposta = cliente.post(
        "/consultas", json={"medico_id": 1, "inicio": horario_livre}, headers=paciente
    )
    assert resposta.status_code == 201

    dia = horario_livre[:10]
    livres = cliente.get(f"/medicos/1/horarios?data={dia}", headers=paciente).json()
    assert horario_livre not in livres


def test_dois_pacientes_nao_marcam_o_mesmo_horario(paciente, outro_paciente, horario_livre):
    pedido = {"medico_id": 1, "inicio": horario_livre}
    assert cliente.post("/consultas", json=pedido, headers=paciente).status_code == 201
    assert cliente.post("/consultas", json=pedido, headers=outro_paciente).status_code == 409


def test_banco_recusa_horario_duplicado():
    """Mesmo pulando as validações da API, o índice único do banco barra a duplicata."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    inicio = api.datetime(2030, 1, 7, 8, 0)
    with Session(api.engine) as db:
        db.add(api.Consulta(medico_id=1, paciente_id=1, inicio=inicio))
        db.commit()
        db.add(api.Consulta(medico_id=1, paciente_id=2, inicio=inicio))
        with pytest.raises(IntegrityError):
            db.commit()


def test_nao_marca_fora_da_agenda_nem_no_passado(paciente):
    fora = {"medico_id": 1, "inicio": f"{proxima_segunda()}T15:00:00"}
    passado = {"medico_id": 1, "inicio": "2020-01-06T08:00:00"}
    assert cliente.post("/consultas", json=fora, headers=paciente).status_code == 409
    assert cliente.post("/consultas", json=passado, headers=paciente).status_code == 409


def test_paciente_nao_marca_dois_medicos_no_mesmo_horario(paciente):
    # Quinta-feira às 09:00: a Dra. Ana (1) e a Dra. Carla (3) atendem.
    quinta = proxima_segunda() + timedelta(days=3)
    inicio = f"{quinta}T09:00:00"
    primeira = cliente.post("/consultas", json={"medico_id": 1, "inicio": inicio}, headers=paciente)
    segunda = cliente.post("/consultas", json={"medico_id": 3, "inicio": inicio}, headers=paciente)
    assert primeira.status_code == 201
    assert segunda.status_code == 409


def test_cancelar_libera_o_horario(paciente, outro_paciente, horario_livre):
    pedido = {"medico_id": 1, "inicio": horario_livre}
    consulta = cliente.post("/consultas", json=pedido, headers=paciente).json()

    # Só o dono da consulta pode cancelar.
    assert cliente.delete(f"/consultas/{consulta['id']}", headers=outro_paciente).status_code == 404
    assert cliente.delete(f"/consultas/{consulta['id']}", headers=paciente).status_code == 204

    assert cliente.get("/consultas", headers=paciente).json() == []
    assert cliente.post("/consultas", json=pedido, headers=outro_paciente).status_code == 201
