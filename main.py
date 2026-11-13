import keyboard
import sounddevice as sd
import numpy as np
import webview
import os
import threading
import speech_recognition as sr
import json
import base64
from groq import Groq
import pyttsx3
import httpx
import queue  # Importado para gerenciar a fila de fala de forma segura

# Definições de controle e variáveis de voz locais
CONFIG_VOZ = {
    "id_voz": None,
    "velocidade": 175,
    "volume": 1.0
}

historico_conversa = []
contador_complementar = 0
microfone_bloqueado = False

# Fila para gerenciar as falas de maneira segura entre threads
fila_fala = queue.Queue()

# Configuração do cliente Groq com a sua chave
client = Groq(
    api_key=(
        "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"
    ),
    http_client=httpx.Client(proxy=None),
)

gravando = False
audio_data = []          
samplerate = 16000       
channels = 1
janela = None  

diretorio_atual = os.path.dirname(os.path.abspath(__file__))
caminho_html = os.path.join(diretorio_atual, 'index.html')

# Inicializa o motor de voz local do sistema
voz = pyttsx3.init()

vozes_sistema = []
for v in voz.getProperty('voices'):
    vozes_sistema.append({"id": v.id, "nome": v.name})

if vozes_sistema:
    CONFIG_VOZ["id_voz"] = vozes_sistema[0]["id"]


# Função que roda em segundo plano consumindo a fila de falas sem quebrar o loop do pyttsx3
def worker_fala():
    while True:
        texto = fila_fala.get()
        if texto is None:
            break
        try:
            # Configura as propriedades de forma segura antes de falar
            if CONFIG_VOZ["id_voz"]:
                voz.setProperty('voice', CONFIG_VOZ["id_voz"])
            voz.setProperty('rate', CONFIG_VOZ["velocidade"])
            voz.setProperty('volume', CONFIG_VOZ["volume"])
            
            voz.say(texto)
            voz.runAndWait()
        except Exception as e:
            print(f"❌ Erro ao sintetizar voz: {e}")
        finally:
            fila_fala.task_done()

# Inicia a thread responsável pela fila de fala
threading.Thread(target=worker_fala, daemon=True).start()


# Ponte de Chamadas (O JavaScript do seu HTML chama o Python por aqui)
class MinhaPonte:
    def atualizar_configuracoes_voz(self, id_voz, velocidade, volume):
        CONFIG_VOZ["id_voz"] = id_voz
        CONFIG_VOZ["velocidade"] = velocidade
        CONFIG_VOZ["volume"] = volume
        print(
            "⚙️ Configurações Atualizadas -> "
            f"ID Voz: {id_voz} | Velocidade: {velocidade} | "
            f"Volume: {volume}"
        )
        return True

    def reiniciar_conversa_backend(self):
        global historico_conversa, contador_complementar, microfone_bloqueado
        historico_conversa = []
        contador_complementar = 0
        microfone_bloqueado = False
        print("🔄 Histórico de conversa reiniciado e limpo no Python.")
        return True

    def bloquear_microfone(self, status):
        global microfone_bloqueado
        microfone_bloqueado = status
        return True


def callback(indata, frames, time, status):
    if gravando:
        audio_data.append(indata.copy())


def enviar_para_tela_com_seguranca(texto, remetente):
    if janela:
        texto_bytes = texto.encode('utf-8')
        base64_texto = base64.b64encode(texto_bytes).decode('utf-8')
        janela.evaluate_js(
            "window.injetarMensagemSegura("
            f"'{base64_texto}', '{remetente}'"
            ")"
        )


def processar_audio_e_ia(audio_final):
    global contador_complementar, microfone_bloqueado
    try:
        if janela:
            janela.evaluate_js(
                "window.atualizarStatus('Processando áudio...', '#f1c40f')"
            )

        recon = sr.Recognizer()
        dados_audio = (audio_final * 32767).astype(np.int16).tobytes()
        audio_data_obj = sr.AudioData(dados_audio, samplerate, 2)

        texto = recon.recognize_google(audio_data_obj, language="pt-BR")

        # Envia o texto reconhecido para a interface
        enviar_para_tela_com_seguranca(texto, 'user')

        if janela:
            janela.evaluate_js(
                "window.atualizarStatus('IA pensando...', '#3498db')"
            )

        # Adiciona a mensagem ao histórico para manter o contexto
        historico_conversa.append({"role": "user", "content": texto})

        # Faz a requisição à API usando o modelo configurado
        resposta = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=historico_conversa,
        )

        texto_da_ia = resposta.choices[0].message.content
        historico_conversa.append({"role": "assistant", "content": texto_da_ia})

        # Exibe a resposta na tela de forma segura
        enviar_para_tela_com_seguranca(texto_da_ia, 'bot')
        
        # Controla a contagem do fluxo de perguntas complementares
        if len(historico_conversa) > 2:
            contador_complementar += 1

        limite_atingido = (contador_complementar >= 3)
        if limite_atingido:
            microfone_bloqueado = True

        if janela:
            janela.evaluate_js(
                f"window.atualizarFluxoInterface({contador_complementar}, "
                f"{'true' if limite_atingido else 'false'})"
            )
            if not limite_atingido:
                janela.evaluate_js(
                    "window.atualizarStatus('Aguardando ação...', '#95a5a6')"
                )
            else:
                janela.evaluate_js(
                    "window.atualizarStatus('Conversa Encerrada', '#e74c3c')"
                )

        # Em vez de chamar o motor diretamente e quebrar as threads, adicionamos o texto à fila segura
        fila_fala.put(texto_da_ia)

    except sr.UnknownValueError:
        if janela:
            janela.evaluate_js(
                "window.atualizarStatus("
                "'Não entendi o áudio. Tente de novo.', '#e74c3c')"
            )
    except Exception as erro_api:
        print("\n❌ ERRO NO FLUXO COMPLETO:", erro_api)
        if janela:
            janela.evaluate_js(
                "window.atualizarStatus('Erro ao carregar resposta.', "
                "'#e74c3c')"
            )


def activar_assistente():
    global gravando, audio_data

    if microfone_bloqueado:
        print("Microfone bloqueado temporariamente pelo fluxo de regras.")
        return

    if not gravando:
        gravando = True
        audio_data = []
        if janela:
            janela.evaluate_js(
                "window.atualizarStatus('🎤 Gravando áudio...', '#e74c3c')"
            )
    else:
        gravando = False

        if audio_data:
            audio_final = np.concatenate(audio_data, axis=0)
            threading.Thread(
                target=processar_audio_e_ia,
                args=(audio_final,),
                daemon=True,
            ).start()
        else:
            if janela:
                janela.evaluate_js(
                    "window.atualizarStatus('Nenhum som detectado.', '#e74c3c')"
                )


def iniciar_microfone_bg():
    keyboard.add_hotkey('insert', activar_assistente)
    with sd.InputStream(
        samplerate=samplerate, channels=channels, callback=callback
    ):
        keyboard.wait()


def quando_janela_carregar(window):
    """Envia as vozes disponíveis do Windows ao HTML quando ele abrir."""
    vozes_json_str = json.dumps(vozes_sistema)
    window.evaluate_js(f"window.carregarVozesDisponiveis({vozes_json_str})")


# Inicia a escuta do teclado em segundo plano
threading.Thread(target=iniciar_microfone_bg, daemon=True).start()

ponte_api = MinhaPonte()

# Configuração e abertura da janela com PyWebView
janela = webview.create_window(
    title='Meu Aplicativo Desktop',
    url=caminho_html,
    js_api=ponte_api,
    width=1000,
    height=700,
)

janela.events.loaded += quando_janela_carregar
webview.start()
