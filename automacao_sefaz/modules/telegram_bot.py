"""
Modulo Telegram Bot para o Aero.
Funcionalidades:
- Enviar notificacoes de progresso (inicio, fim, erros)
- Receber comandos remotos (/status, /iniciar, /parar, /voos)
- Thread separada para polling de mensagens

Uso:
    from modules.telegram_bot import TelegramBot
    bot = TelegramBot(token, chat_id)
    bot.iniciar()
    bot.notificar("Processamento iniciado!")
    bot.parar()

Requisito: pip install python-telegram-bot
"""

import logging
import threading
import time
import queue
from datetime import datetime
from typing import Optional, Callable, Dict, List, Any

try:
    import telegram
    from telegram import Update, Bot
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        ContextTypes, filters,
    )
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Bot Telegram para notificacoes e comandos remotos do Aero.

    Notificacoes enviadas:
    - Inicio de processamento (quantos voos)
    - Progresso por voo (concluido/erro)
    - Fim do processamento (resumo)
    - Erros criticos

    Comandos aceitos:
    - /status  -> Estado atual (processando/idle, ultimo resultado)
    - /iniciar -> Dispara processamento remoto (usa datas de hoje)
    - /parar   -> Cancela processamento em andamento
    - /voos    -> Lista voos encontrados na ultima busca
    - /ajuda   -> Lista de comandos
    """

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._ativo = False
        self._thread: Optional[threading.Thread] = None
        self._bot: Optional[Any] = None
        self._app: Optional[Any] = None

        # Callbacks que o main.py pode registrar
        self._callback_iniciar: Optional[Callable] = None
        self._callback_parar: Optional[Callable] = None
        self._callback_status: Optional[Callable[[], str]] = None
        self._callback_voos: Optional[Callable[[], str]] = None

        # Fila de comandos recebidos (para processamento no main thread)
        self.fila_comandos: queue.Queue = queue.Queue()

        # Estado interno para respostas
        self._ultimo_resumo: str = ""
        self._processando: bool = False

    @property
    def configurado(self) -> bool:
        """Verifica se o bot tem token e chat_id configurados."""
        return bool(self.bot_token and self.chat_id)

    @property
    def disponivel(self) -> bool:
        """Verifica se a lib python-telegram-bot esta instalada."""
        return HAS_TELEGRAM

    def registrar_callbacks(self,
                            cb_iniciar: Optional[Callable] = None,
                            cb_parar: Optional[Callable] = None,
                            cb_status: Optional[Callable[[], str]] = None,
                            cb_voos: Optional[Callable[[], str]] = None):
        """Registra callbacks do app principal para os comandos remotos."""
        self._callback_iniciar = cb_iniciar
        self._callback_parar = cb_parar
        self._callback_status = cb_status
        self._callback_voos = cb_voos

    # ========= ENVIO DE NOTIFICACOES =========

    def notificar(self, mensagem: str):
        """Envia uma mensagem de notificacao (thread-safe, nao bloqueia)."""
        if not self.configurado or not self._ativo:
            return

        threading.Thread(
            target=self._enviar_mensagem,
            args=(mensagem,),
            daemon=True
        ).start()

    def notificar_inicio(self, total_voos: int, voos_nomes: List[str]):
        """Notifica inicio de processamento."""
        lista = "\n".join(f"  - {v}" for v in voos_nomes[:10])
        msg = (
            f"\u2708\ufe0f *AERO - Processamento Iniciado*\n\n"
            f"Voos: *{total_voos}*\n"
            f"{lista}\n\n"
            f"\u23f0 {datetime.now().strftime('%H:%M:%S')}"
        )
        self.notificar(msg)
        self._processando = True

    def notificar_voo_concluido(self, indice: int, total: int,
                                 nome_voo: str, liberados: int, retidos: int):
        """Notifica conclusao de um voo."""
        msg = (
            f"\u2705 *Voo {indice}/{total}:* {nome_voo}\n"
            f"  Liberados: {liberados} | Retidos: {retidos}"
        )
        self.notificar(msg)

    def notificar_voo_erro(self, indice: int, total: int,
                            nome_voo: str, erro: str):
        """Notifica erro em um voo."""
        msg = (
            f"\u274c *Voo {indice}/{total}:* {nome_voo}\n"
            f"  Erro: {erro[:100]}"
        )
        self.notificar(msg)

    def notificar_fim(self, total_voos: int, sucesso: int, erros: int,
                       total_liberados: int, total_retidos: int):
        """Notifica fim do processamento com resumo."""
        emoji = "\u2705" if erros == 0 else "\u26a0\ufe0f"
        msg = (
            f"{emoji} *AERO - Processamento Concluido*\n\n"
            f"Voos: {total_voos} ({sucesso} OK, {erros} erros)\n"
            f"AWBs liberados: *{total_liberados}*\n"
            f"AWBs retidos: *{total_retidos}*\n\n"
            f"\u23f0 {datetime.now().strftime('%H:%M:%S')}"
        )
        self.notificar(msg)
        self._processando = False
        self._ultimo_resumo = msg

    def notificar_erro_critico(self, erro: str):
        """Notifica erro critico que interrompeu o processamento."""
        msg = (
            f"\U0001f6a8 *AERO - ERRO CRITICO*\n\n"
            f"{erro[:200]}\n\n"
            f"\u23f0 {datetime.now().strftime('%H:%M:%S')}"
        )
        self.notificar(msg)
        self._processando = False

    # ========= POLLING DE COMANDOS (LONG POLLING) =========

    def iniciar(self):
        """Inicia o bot em thread separada (polling de comandos)."""
        if not self.configurado:
            logger.warning("Telegram Bot: token ou chat_id nao configurados")
            return

        if not HAS_TELEGRAM:
            logger.warning("Telegram Bot: python-telegram-bot nao instalado")
            return

        self._ativo = True
        self._thread = threading.Thread(target=self._loop_polling, daemon=True)
        self._thread.start()
        logger.info("Telegram Bot iniciado (polling)")

    def parar(self):
        """Para o bot."""
        self._ativo = False
        if self._app:
            try:
                self._app.stop()
            except Exception:
                pass
        logger.info("Telegram Bot parado")

    def _loop_polling(self):
        """Loop de polling simples usando requests (sem asyncio complexo)."""
        import requests

        url_base = f"https://api.telegram.org/bot{self.bot_token}"
        offset = 0

        logger.info("Telegram polling iniciado")

        while self._ativo:
            try:
                resp = requests.get(
                    f"{url_base}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=35
                )

                if resp.status_code != 200:
                    logger.warning(f"Telegram polling erro HTTP {resp.status_code}")
                    time.sleep(5)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    time.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    self._processar_update(update)

            except Exception as e:
                if self._ativo:
                    logger.debug(f"Telegram polling erro: {e}")
                    time.sleep(5)

        logger.info("Telegram polling encerrado")

    def _processar_update(self, update: dict):
        """Processa um update recebido do Telegram."""
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id_msg = str(msg.get("chat", {}).get("id", ""))

        # Seguranca: so aceita comandos do chat_id configurado
        if chat_id_msg != str(self.chat_id):
            logger.debug(f"Telegram: mensagem de chat nao autorizado ({chat_id_msg})")
            return

        if not text.startswith("/"):
            return

        comando = text.split()[0].lower().replace("@", "").split("@")[0]
        logger.info(f"Telegram comando recebido: {comando}")

        if comando == "/status":
            self._responder_status()
        elif comando == "/iniciar":
            self._responder_iniciar()
        elif comando == "/parar":
            self._responder_parar()
        elif comando == "/voos":
            self._responder_voos()
        elif comando in ("/ajuda", "/help", "/start"):
            self._responder_ajuda()
        else:
            self._enviar_mensagem(
                f"Comando desconhecido: `{comando}`\n"
                f"Use /ajuda para ver comandos disponiveis."
            )

    # ========= RESPOSTAS A COMANDOS =========

    def _responder_status(self):
        """Responde ao /status."""
        if self._callback_status:
            try:
                texto = self._callback_status()
                self._enviar_mensagem(texto)
                return
            except Exception as e:
                logger.error(f"Callback status erro: {e}")

        # Fallback se nao tem callback
        if self._processando:
            self._enviar_mensagem("\u23f3 *Processando...*\nUse /parar para cancelar.")
        elif self._ultimo_resumo:
            self._enviar_mensagem(f"*Ultimo resultado:*\n{self._ultimo_resumo}")
        else:
            self._enviar_mensagem("\U0001f4a4 *Idle* - Nenhum processamento recente.")

    def _responder_iniciar(self):
        """Responde ao /iniciar."""
        if self._processando:
            self._enviar_mensagem("\u26a0\ufe0f Ja existe processamento em andamento!")
            return

        if self._callback_iniciar:
            self._enviar_mensagem("\u2708\ufe0f Iniciando processamento remoto...")
            # Enfileira o comando para o main thread processar
            self.fila_comandos.put({"cmd": "iniciar"})
            try:
                self._callback_iniciar()
            except Exception as e:
                self._enviar_mensagem(f"\u274c Erro ao iniciar: {e}")
        else:
            self._enviar_mensagem(
                "\u274c Comando /iniciar nao disponivel.\n"
                "Busque os voos na interface primeiro."
            )

    def _responder_parar(self):
        """Responde ao /parar."""
        if not self._processando:
            self._enviar_mensagem("\u2139\ufe0f Nenhum processamento em andamento.")
            return

        if self._callback_parar:
            self.fila_comandos.put({"cmd": "parar"})
            try:
                self._callback_parar()
                self._enviar_mensagem("\u26d4 Solicitacao de parada enviada.")
            except Exception as e:
                self._enviar_mensagem(f"\u274c Erro ao parar: {e}")
        else:
            self._enviar_mensagem("\u274c Funcao parar nao configurada.")

    def _responder_voos(self):
        """Responde ao /voos."""
        if self._callback_voos:
            try:
                texto = self._callback_voos()
                self._enviar_mensagem(texto)
                return
            except Exception as e:
                logger.error(f"Callback voos erro: {e}")

        self._enviar_mensagem("\u2139\ufe0f Nenhum voo na memoria. Busque na interface primeiro.")

    def _responder_ajuda(self):
        """Responde ao /ajuda."""
        msg = (
            "\U0001f916 *AERO Bot - Comandos*\n\n"
            "/status - Estado atual do processamento\n"
            "/iniciar - Iniciar processamento (voos de hoje)\n"
            "/parar - Cancelar processamento\n"
            "/voos - Listar voos encontrados\n"
            "/ajuda - Esta mensagem\n\n"
            "\U0001f4e1 Notificacoes automaticas de inicio/fim/erros"
        )
        self._enviar_mensagem(msg)

    # ========= BAIXO NIVEL =========

    def _enviar_mensagem(self, texto: str):
        """Envia mensagem para o chat configurado (bloqueante)."""
        if not self.configurado:
            return

        try:
            import requests
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": texto,
                "parse_mode": "Markdown",
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Telegram envio falhou: {resp.status_code} - {resp.text[:100]}")
        except Exception as e:
            logger.error(f"Telegram envio erro: {e}")

    def testar_conexao(self) -> bool:
        """Testa se o bot consegue enviar mensagem."""
        if not self.configurado:
            return False

        try:
            import requests
            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200 and resp.json().get("ok"):
                bot_info = resp.json()["result"]
                logger.info(f"Telegram Bot OK: @{bot_info.get('username', '?')}")
                return True
            return False
        except Exception as e:
            logger.error(f"Telegram teste falhou: {e}")
            return False
