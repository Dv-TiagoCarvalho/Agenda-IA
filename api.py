"""API da Agenda: usuários, médicos, horários livres e consultas.

Rodar:  uvicorn api:app --reload
Testar: http://127.0.0.1:8000/docs
"""

import hashlib
import hmac
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Index, String, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

# ---------------------------------------------------------------- configuração

# Em produção, definir a variável de ambiente SECRET_KEY com um valor secreto.
SECRET_KEY = os.environ.get("SECRET_KEY", "chave-apenas-para-desenvolvimento-troque-em-producao")
ALGORITMO = "HS256"
VALIDADE_TOKEN = timedelta(hours=8)

# Por padrão usa um arquivo SQLite ao lado deste. Para outro banco (PostgreSQL,
# por exemplo), definir a variável de ambiente DATABASE_URL.
ARQUIVO_BANCO = Path(__file__).parent / "agenda.db"
engine = create_engine(os.environ.get("DATABASE_URL", f"sqlite:///{ARQUIVO_BANCO}"))

# ---------------------------------------------------------------------- banco


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(200))
    papel: Mapped[str] = mapped_column(String(20), default="paciente")


class Medico(Base):
    __tablename__ = "medicos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100))
    especialidade: Mapped[str] = mapped_column(String(100))
    duracao_min: Mapped[int] = mapped_column(default=30)


class Disponibilidade(Base):
    """Período em que o médico atende em um dia da semana (0 = segunda)."""

    __tablename__ = "disponibilidades"

    id: Mapped[int] = mapped_column(primary_key=True)
    medico_id: Mapped[int] = mapped_column(ForeignKey("medicos.id"), index=True)
    dia_semana: Mapped[int]
    hora_inicio: Mapped[time]
    hora_fim: Mapped[time]


class Consulta(Base):
    __tablename__ = "consultas"
    # Regra garantida pelo banco: um médico não pode ter duas consultas
    # agendadas no mesmo horário, mesmo com dois pedidos ao mesmo tempo.
    __table_args__ = (
        Index(
            "uq_consulta_medico_inicio",
            "medico_id",
            "inicio",
            unique=True,
            sqlite_where=text("status = 'agendada'"),
            postgresql_where=text("status = 'agendada'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    medico_id: Mapped[int] = mapped_column(ForeignKey("medicos.id"), index=True)
    paciente_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    inicio: Mapped[datetime]  # horário local da clínica
    status: Mapped[str] = mapped_column(String(20), default="agendada")


Base.metadata.create_all(engine)


def criar_dados_de_exemplo():
    """Cadastra médicos fictícios na primeira vez, para o app ter o que mostrar."""
    exemplos = [
        ("Dra. Ana Lima", "Clínica geral", [0, 1, 2, 3, 4], time(8), time(12)),
        ("Dr. Bruno Reis", "Cardiologia", [0, 2, 4], time(13), time(17)),
        ("Dra. Carla Nunes", "Dermatologia", [1, 3], time(9), time(16)),
    ]
    with Session(engine) as db:
        if db.scalar(select(Medico)):
            return
        for nome, especialidade, dias, inicio, fim in exemplos:
            medico = Medico(nome=nome, especialidade=especialidade)
            db.add(medico)
            db.flush()
            for dia in dias:
                db.add(
                    Disponibilidade(
                        medico_id=medico.id, dia_semana=dia, hora_inicio=inicio, hora_fim=fim
                    )
                )
        db.commit()


criar_dados_de_exemplo()


def get_db():
    with Session(engine) as db:
        yield db


# --------------------------------------------------------------------- senhas
# A senha nunca é guardada: só o hash dela, gerado com scrypt e um salt aleatório.


def gerar_hash(senha: str) -> str:
    salt = os.urandom(16)
    chave = hashlib.scrypt(senha.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${chave.hex()}"


def senha_confere(senha: str, senha_hash: str) -> bool:
    salt_hex, chave_hex = senha_hash.split("$")
    chave = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(chave.hex(), chave_hex)


# --------------------------------------------------------------------- tokens


def criar_token(usuario_id: int) -> str:
    dados = {"sub": str(usuario_id), "exp": datetime.now(timezone.utc) + VALIDADE_TOKEN}
    return jwt.encode(dados, SECRET_KEY, algorithm=ALGORITMO)


def usuario_logado(
    credenciais: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db),
) -> Usuario:
    erro = HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado.")
    try:
        dados = jwt.decode(credenciais.credentials, SECRET_KEY, algorithms=[ALGORITMO])
    except jwt.PyJWTError:
        raise erro
    usuario = db.get(Usuario, int(dados["sub"]))
    if usuario is None:
        raise erro
    return usuario


# ------------------------------------------------------- formatos de entrada/saída


class UsuarioCriar(BaseModel):
    nome: str = Field(min_length=2, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    senha: str = Field(min_length=6, max_length=100)


class UsuarioSaida(BaseModel):
    id: int
    nome: str
    email: str
    papel: str


class LoginEntrada(BaseModel):
    email: str
    senha: str


class TokenSaida(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MedicoSaida(BaseModel):
    id: int
    nome: str
    especialidade: str
    duracao_min: int


class ConsultaCriar(BaseModel):
    medico_id: int
    inicio: datetime


class ConsultaSaida(BaseModel):
    id: int
    inicio: datetime
    status: str
    medico_nome: str
    especialidade: str


# ------------------------------------------------------------ regra da agenda


def horarios_livres(db: Session, medico: Medico, dia: date) -> list[datetime]:
    """Horários livres = disponibilidade do médico - consultas marcadas - passado.

    Nada disso fica salvo: é calculado na hora, a cada consulta à agenda.
    """
    comeco_do_dia = datetime.combine(dia, time.min)
    ocupados = set(
        db.scalars(
            select(Consulta.inicio).where(
                Consulta.medico_id == medico.id,
                Consulta.status == "agendada",
                Consulta.inicio >= comeco_do_dia,
                Consulta.inicio < comeco_do_dia + timedelta(days=1),
            )
        )
    )
    periodos = db.scalars(
        select(Disponibilidade)
        .where(Disponibilidade.medico_id == medico.id, Disponibilidade.dia_semana == dia.weekday())
        .order_by(Disponibilidade.hora_inicio)
    )
    agora = datetime.now()
    passo = timedelta(minutes=medico.duracao_min)
    livres = []
    for periodo in periodos:
        horario = datetime.combine(dia, periodo.hora_inicio)
        fim = datetime.combine(dia, periodo.hora_fim)
        while horario + passo <= fim:
            if horario > agora and horario not in ocupados:
                livres.append(horario)
            horario += passo
    return livres


def buscar_medico(medico_id: int, db: Session) -> Medico:
    medico = db.get(Medico, medico_id)
    if medico is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Médico não encontrado.")
    return medico


# ---------------------------------------------------------------------- rotas

app = FastAPI(title="API da Agenda")


@app.post("/usuarios", response_model=UsuarioSaida, status_code=status.HTTP_201_CREATED)
def criar_usuario(dados: UsuarioCriar, db: Session = Depends(get_db)):
    email = dados.email.strip().lower()
    if db.scalar(select(Usuario).where(Usuario.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Este e-mail já está cadastrado.")
    usuario = Usuario(nome=dados.nome.strip(), email=email, senha_hash=gerar_hash(dados.senha))
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@app.post("/login", response_model=TokenSaida)
def login(dados: LoginEntrada, db: Session = Depends(get_db)):
    email = dados.email.strip().lower()
    usuario = db.scalar(select(Usuario).where(Usuario.email == email))
    if usuario is None or not senha_confere(dados.senha, usuario.senha_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-mail ou senha incorretos.")
    return TokenSaida(access_token=criar_token(usuario.id))


@app.get("/eu", response_model=UsuarioSaida)
def meus_dados(usuario: Usuario = Depends(usuario_logado)):
    return usuario


@app.get("/medicos", response_model=list[MedicoSaida])
def listar_medicos(_: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    return db.scalars(select(Medico).order_by(Medico.nome)).all()


@app.get("/medicos/{medico_id}/horarios", response_model=list[datetime])
def listar_horarios(
    medico_id: int,
    data: date,
    _: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    return horarios_livres(db, buscar_medico(medico_id, db), data)


@app.post("/consultas", response_model=ConsultaSaida, status_code=status.HTTP_201_CREATED)
def marcar_consulta(
    dados: ConsultaCriar,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    medico = buscar_medico(dados.medico_id, db)
    inicio = dados.inicio.replace(second=0, microsecond=0, tzinfo=None)

    if inicio not in horarios_livres(db, medico, inicio.date()):
        raise HTTPException(status.HTTP_409_CONFLICT, "Este horário não está disponível.")

    ja_tem = db.scalar(
        select(Consulta).where(
            Consulta.paciente_id == usuario.id,
            Consulta.inicio == inicio,
            Consulta.status == "agendada",
        )
    )
    if ja_tem:
        raise HTTPException(status.HTTP_409_CONFLICT, "Você já tem uma consulta neste horário.")

    consulta = Consulta(medico_id=medico.id, paciente_id=usuario.id, inicio=inicio)
    db.add(consulta)
    try:
        db.commit()
    except IntegrityError:  # outra pessoa marcou o mesmo horário no mesmo instante
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Este horário acabou de ser ocupado.")
    db.refresh(consulta)
    return ConsultaSaida(
        id=consulta.id,
        inicio=consulta.inicio,
        status=consulta.status,
        medico_nome=medico.nome,
        especialidade=medico.especialidade,
    )


@app.get("/consultas", response_model=list[ConsultaSaida])
def minhas_consultas(usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    linhas = db.execute(
        select(Consulta, Medico)
        .join(Medico, Medico.id == Consulta.medico_id)
        .where(
            Consulta.paciente_id == usuario.id,
            Consulta.status == "agendada",
            Consulta.inicio > datetime.now(),
        )
        .order_by(Consulta.inicio)
    )
    return [
        ConsultaSaida(
            id=consulta.id,
            inicio=consulta.inicio,
            status=consulta.status,
            medico_nome=medico.nome,
            especialidade=medico.especialidade,
        )
        for consulta, medico in linhas
    ]


@app.delete("/consultas/{consulta_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancelar_consulta(
    consulta_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    consulta = db.get(Consulta, consulta_id)
    if consulta is None or consulta.paciente_id != usuario.id or consulta.status != "agendada":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consulta não encontrada.")
    if consulta.inicio <= datetime.now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Esta consulta já passou.")
    consulta.status = "cancelada"
    db.commit()
