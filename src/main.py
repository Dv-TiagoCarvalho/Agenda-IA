import inspect
from datetime import date, datetime, timedelta

import flet as ft
import httpx

# Endereço da API. No computador é este; para testar no celular,
# trocar 127.0.0.1 pelo IP do computador na rede (ex.: 192.168.0.10).
API_URL = "http://127.0.0.1:8000"

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
LARGURA = 320


def data_por_extenso(dia):
    return f"{DIAS[dia.weekday()]}, {dia:%d/%m}"


async def chamar_api(metodo, caminho, token=None, **dados):
    """Faz uma chamada à API e devolve (deu_certo, resposta)."""
    cabecalhos = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(base_url=API_URL, timeout=10) as cliente:
            resposta = await cliente.request(
                metodo, caminho, json=dados or None, headers=cabecalhos
            )
    except httpx.HTTPError:
        return False, "Não foi possível conectar à API. Ela está rodando?"

    if resposta.is_success:
        return True, resposta.json() if resposta.content else None

    detalhe = resposta.json().get("detail", "Erro inesperado.")
    if isinstance(detalhe, list):  # erro de validação dos campos
        detalhe = "Confira os dados: nome, e-mail válido e senha com 6+ caracteres."
    return False, detalhe


def main(page: ft.Page):
    page.title = "Agenda"
    page.vertical_alignment = ft.MainAxisAlignment.CENTER
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.scroll = ft.ScrollMode.AUTO

    sessao = {"token": None, "usuario": None}

    # ------------------------------------------------------------ utilidades

    def mostrar(*controles):
        """Troca o conteúdo da tela."""
        page.clean()
        page.add(
            ft.SafeArea(
                content=ft.Column(
                    controls=list(controles),
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                )
            )
        )

    def cabecalho(titulo, subtitulo=None):
        itens = [
            ft.Icon(ft.Icons.CALENDAR_MONTH, size=64),
            ft.Text(titulo, size=28, weight=ft.FontWeight.BOLD),
        ]
        if subtitulo:
            itens.append(ft.Text(subtitulo))
        return itens

    def texto_aviso(texto="", cor=ft.Colors.RED):
        return ft.Text(
            texto, color=cor, width=LARGURA, text_align=ft.TextAlign.CENTER
        )

    def ir(tela, *args):
        """Cria a função de clique que abre uma tela."""

        async def ao_clicar(e):
            resultado = tela(*args)
            if inspect.isawaitable(resultado):
                await resultado

        return ao_clicar

    async def api(metodo, caminho, **dados):
        """Chamada à API já com o token de quem está logado."""
        return await chamar_api(metodo, caminho, token=sessao["token"], **dados)

    # ------------------------------------------------------------ tela: login

    def tela_login(aviso=""):
        email = ft.TextField(
            label="E-mail", keyboard_type=ft.KeyboardType.EMAIL, width=LARGURA
        )
        senha = ft.TextField(
            label="Senha", password=True, can_reveal_password=True, width=LARGURA
        )
        mensagem = texto_aviso(aviso, ft.Colors.GREEN)

        async def entrar(e):
            mensagem.color = ft.Colors.RED
            if not email.value or not senha.value:
                mensagem.value = "Preencha e-mail e senha."
                page.update()
                return

            ok, resposta = await chamar_api(
                "POST", "/login", email=email.value, senha=senha.value
            )
            if not ok:
                mensagem.value = resposta
                page.update()
                return

            sessao["token"] = resposta["access_token"]
            ok, usuario = await api("GET", "/eu")
            if not ok:
                mensagem.value = usuario
                page.update()
                return
            sessao["usuario"] = usuario
            tela_inicio()

        mostrar(
            *cabecalho("Agenda"),
            email,
            senha,
            ft.FilledButton("Entrar", on_click=entrar, width=LARGURA),
            ft.TextButton("Criar conta", on_click=ir(tela_cadastro)),
            mensagem,
        )

    # --------------------------------------------------------- tela: cadastro

    def tela_cadastro():
        nome = ft.TextField(label="Nome", width=LARGURA)
        email = ft.TextField(
            label="E-mail", keyboard_type=ft.KeyboardType.EMAIL, width=LARGURA
        )
        senha = ft.TextField(
            label="Senha (mínimo 6 caracteres)",
            password=True,
            can_reveal_password=True,
            width=LARGURA,
        )
        mensagem = texto_aviso()

        async def cadastrar(e):
            if not nome.value or not email.value or not senha.value:
                mensagem.value = "Preencha todos os campos."
                page.update()
                return

            ok, resposta = await chamar_api(
                "POST",
                "/usuarios",
                nome=nome.value,
                email=email.value,
                senha=senha.value,
            )
            if not ok:
                mensagem.value = resposta
                page.update()
                return

            tela_login(aviso="Conta criada! Agora é só entrar.")

        mostrar(
            *cabecalho("Criar conta"),
            nome,
            email,
            senha,
            ft.FilledButton("Cadastrar", on_click=cadastrar, width=LARGURA),
            ft.TextButton("Já tenho conta", on_click=ir(tela_login)),
            mensagem,
        )

    # ----------------------------------------------------------- tela: início

    def tela_inicio():
        def sair(e):
            sessao["token"] = None
            sessao["usuario"] = None
            tela_login()

        mostrar(
            *cabecalho(f"Olá, {sessao['usuario']['nome']}!", "O que você quer fazer?"),
            ft.FilledButton(
                "Agendar consulta",
                icon=ft.Icons.ADD,
                on_click=ir(tela_medicos),
                width=LARGURA,
            ),
            ft.FilledTonalButton(
                "Minhas consultas",
                icon=ft.Icons.EVENT_AVAILABLE,
                on_click=ir(tela_consultas),
                width=LARGURA,
            ),
            ft.TextButton("Sair", on_click=sair),
        )

    # ---------------------------------------------------------- tela: médicos

    async def tela_medicos():
        ok, medicos = await api("GET", "/medicos")
        if not ok:
            mostrar(
                *cabecalho("Médicos"),
                texto_aviso(medicos),
                ft.TextButton("Voltar", on_click=ir(tela_inicio)),
            )
            return

        cartoes = [
            ft.Card(
                width=LARGURA,
                content=ft.ListTile(
                    leading=ft.Icon(ft.Icons.PERSON),
                    title=ft.Text(medico["nome"]),
                    subtitle=ft.Text(medico["especialidade"]),
                    trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT),
                    on_click=ir(tela_horarios, medico),
                ),
            )
            for medico in medicos
        ]
        mostrar(
            *cabecalho("Médicos", "Escolha com quem quer se consultar"),
            *cartoes,
            ft.TextButton("Voltar", on_click=ir(tela_inicio)),
        )

    # --------------------------------------------------------- tela: horários

    async def tela_horarios(medico, dia=None):
        async def buscar(d):
            return await api(
                "GET", f"/medicos/{medico['id']}/horarios?data={d.isoformat()}"
            )

        hoje = date.today()
        if dia is None:
            # Ao abrir, pula para o primeiro dia que tem horário livre.
            dia = hoje
            for _ in range(14):
                ok, horarios = await buscar(dia)
                if not ok or horarios:
                    break
                dia += timedelta(days=1)
        else:
            ok, horarios = await buscar(dia)

        if not ok:
            mostrar(
                *cabecalho(medico["nome"]),
                texto_aviso(horarios),
                ft.TextButton("Voltar", on_click=ir(tela_medicos)),
            )
            return

        botoes = [
            ft.OutlinedButton(
                horario[11:16],
                on_click=ir(tela_confirmar, medico, datetime.fromisoformat(horario)),
            )
            for horario in horarios
        ]
        mostrar(
            *cabecalho(medico["nome"], medico["especialidade"]),
            ft.Row(
                width=LARGURA,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.IconButton(
                        ft.Icons.CHEVRON_LEFT,
                        tooltip="Dia anterior",
                        disabled=dia <= hoje,
                        on_click=ir(tela_horarios, medico, dia - timedelta(days=1)),
                    ),
                    ft.Text(data_por_extenso(dia), size=18),
                    ft.IconButton(
                        ft.Icons.CHEVRON_RIGHT,
                        tooltip="Próximo dia",
                        on_click=ir(tela_horarios, medico, dia + timedelta(days=1)),
                    ),
                ],
            ),
            ft.Row(
                width=LARGURA,
                wrap=True,
                alignment=ft.MainAxisAlignment.CENTER,
                controls=botoes,
            )
            if botoes
            else ft.Text("Sem horários livres neste dia."),
            ft.TextButton("Voltar", on_click=ir(tela_medicos)),
        )

    # -------------------------------------------------------- tela: confirmar

    def tela_confirmar(medico, inicio):
        mensagem = texto_aviso()

        async def confirmar(e):
            ok, resposta = await api(
                "POST",
                "/consultas",
                medico_id=medico["id"],
                inicio=inicio.isoformat(),
            )
            if not ok:
                mensagem.value = resposta
                page.update()
                return
            await tela_consultas(aviso="Consulta marcada!")

        mostrar(
            *cabecalho("Confirmar consulta"),
            ft.Text(medico["nome"], size=18, weight=ft.FontWeight.BOLD),
            ft.Text(medico["especialidade"]),
            ft.Text(f"{data_por_extenso(inicio.date())} às {inicio:%H:%M}", size=18),
            ft.FilledButton("Confirmar", on_click=confirmar, width=LARGURA),
            ft.TextButton(
                "Escolher outro horário",
                on_click=ir(tela_horarios, medico, inicio.date()),
            ),
            mensagem,
        )

    # ------------------------------------------------- tela: minhas consultas

    async def tela_consultas(aviso="", cor=ft.Colors.GREEN):
        ok, consultas = await api("GET", "/consultas")
        if not ok:
            mostrar(
                *cabecalho("Minhas consultas"),
                texto_aviso(consultas),
                ft.TextButton("Voltar", on_click=ir(tela_inicio)),
            )
            return

        async def cancelar(consulta):
            ok, resposta = await api("DELETE", f"/consultas/{consulta['id']}")
            if ok:
                await tela_consultas(aviso="Consulta cancelada.")
            else:
                await tela_consultas(aviso=resposta, cor=ft.Colors.RED)

        cartoes = []
        for consulta in consultas:
            inicio = datetime.fromisoformat(consulta["inicio"])
            cartoes.append(
                ft.Card(
                    width=LARGURA,
                    content=ft.ListTile(
                        title=ft.Text(
                            f"{data_por_extenso(inicio.date())} às {inicio:%H:%M}"
                        ),
                        subtitle=ft.Text(
                            f"{consulta['medico_nome']} · {consulta['especialidade']}"
                        ),
                        trailing=ft.IconButton(
                            ft.Icons.DELETE_OUTLINE,
                            tooltip="Cancelar consulta",
                            on_click=ir(cancelar, consulta),
                        ),
                    ),
                )
            )

        mostrar(
            *cabecalho("Minhas consultas"),
            *(cartoes or [ft.Text("Você ainda não tem consultas marcadas.")]),
            texto_aviso(aviso, cor),
            ft.FilledButton(
                "Agendar consulta",
                icon=ft.Icons.ADD,
                on_click=ir(tela_medicos),
                width=LARGURA,
            ),
            ft.TextButton("Voltar", on_click=ir(tela_inicio)),
        )

    tela_login()


ft.run(main)
