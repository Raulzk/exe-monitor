import os
import time
import base64
import requests
import argparse
from flask import Flask, jsonify, request
from threading import Thread, Event
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = Flask(__name__)

# Variable global para controlar el observador
observer = None
stop_event = Event()

class DownloadHandler(FileSystemEventHandler):
    def __init__(self, api_url, monitor_path):
        self.api_url = api_url
        self.monitor_path = monitor_path
        self.processed_files = set()
        self.feature_order = [
            'Machine',
            'DebugSize',
            'DebugRVA',
            'MajorImageVersion',
            'MajorOSVersion',
            'ExportRVA',
            'ExportSize',
            'IatVRA',
            'MajorLinkerVersion',
            'MinorLinkerVersion',
            'NumberOfSections',
            'SizeOfStackReserve',
            'DllCharacteristics',
            'ResourceSize',
            'BitcoinAddresses'
        ]
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def on_created(self, event):
        self.check_file(event.src_path, event.is_directory)

    def on_modified(self, event):
        self.check_file(event.src_path, event.is_directory)

    def check_file(self, file_path, is_directory):
        print(f"Evento detectado: {file_path} (is_directory: {is_directory})")
        if is_directory or file_path.endswith(('.tmp', '.crdownload')) or not file_path.lower().endswith('.exe'):
            print(f"Ignorando {file_path}: no es un archivo .exe válido")
            return
        if file_path in self.processed_files:
            print(f"Ignorando {file_path}: ya fue procesado")
            return
        self.processed_files.add(file_path)
        print(f"Nuevo archivo .exe detectado: {file_path}")
        self.process_file(file_path)

    def process_file(self, file_path):
        try:
            print(f"Procesando {file_path}...")
            time.sleep(3)
            if not os.path.exists(file_path):
                print(f"Error: El archivo {file_path} no existe")
                return
            file_size = os.path.getsize(file_path) / (1024 * 1024)
            if file_size > 10:
                print(f"Advertencia: El archivo {file_path} es grande ({file_size:.2f} MB). Puede causar demoras.")
            with open(file_path, 'rb') as f:
                file_content = f.read()
                file_base64 = base64.b64encode(file_content).decode('utf-8')
                print(f"Archivo codificado en base64 (tamaño: {len(file_base64)} caracteres)")

            extract_url = f"{self.api_url}/extract_features"
            print(f"Enviando a {extract_url}")
            response = self.session.post(extract_url, json={'file_base64': file_base64}, timeout=90)

            if response.status_code == 200:
                features = response.json()
                print(f"Características recibidas: {features}")
                feature_values = [features.get(key, 0) for key in self.feature_order]
                print(f"Características ordenadas: {feature_values}")
                predict_url = f"{self.api_url}/predict"
                print(f"Enviando características a {predict_url}")
                predict_response = self.session.post(predict_url, json={'features': feature_values}, timeout=90)

                if predict_response.status_code == 200:
                    prediction = predict_response.json().get('prediction', 'Desconocido')
                    print(f"Predicción para {os.path.basename(file_path)}: {prediction}")
                    with open('predictions.log', 'a') as log_file:
                        log_file.write(f"{time.ctime()}: {os.path.basename(file_path)} -> Predicción: {prediction}\n")
                else:
                    print(f"Error en la predicción: {predict_response.status_code} - {predict_response.text}")
            else:
                print(f"Error al extraer características: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Error al procesar {file_path}: {str(e)}")

def monitor_directory(api_url, monitor_path):
    global observer
    if not os.path.exists(monitor_path):
        os.makedirs(monitor_path)
        print(f"Directorio {monitor_path} creado")
    print(f"Iniciando monitoreo en: {monitor_path}")
    event_handler = DownloadHandler(api_url, monitor_path)
    observer = Observer()
    observer.schedule(event_handler, monitor_path, recursive=False)
    observer.start()
    print(f"Monitoreo activo en {monitor_path}")

    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("Monitoreo detenido")
    observer.join()

@app.route('/start_monitor', methods=['POST'])
def start_monitor():
    global observer
    if observer and observer.is_alive():
        return jsonify({'message': 'El monitoreo ya está activo'}), 400
    data = request.get_json()
    api_url = data.get('api_url', 'https://api-proyecto-w9dn.onrender.com')
    monitor_path = data.get('monitor_path')
    if not monitor_path:
        return jsonify({'error': 'El parámetro monitor_path es obligatorio'}), 400
    try:
        # Validar que monitor_path sea una ruta válida
        if not os.path.isabs(monitor_path):
            return jsonify({'error': 'monitor_path debe ser una ruta absoluta'}), 400
        thread = Thread(target=monitor_directory, args=(api_url, monitor_path))
        thread.start()
        return jsonify({'message': f'Monitoreo iniciado en {monitor_path}'}), 200
    except Exception as e:
        return jsonify({'error': f'Error al iniciar el monitoreo: {str(e)}'}), 500

@app.route('/stop_monitor', methods=['POST'])
def stop_monitor():
    global observer
    if not observer or not observer.is_alive():
        return jsonify({'message': 'El monitoreo no está activo'}), 400
    stop_event.set()
    observer.stop()
    observer.join()
    observer = None
    stop_event.clear()
    return jsonify({'message': 'Monitoreo detenido'}), 200

@app.route('/status', methods=['GET'])
def status():
    global observer
    return jsonify({'monitoring': observer is not None and observer.is_alive()}), 200

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitorea un directorio para .exe y predice usando una API.")
    parser.add_argument('--path', type=str, default="C:\\Users\\wwwab\\Downloads", help="Ruta del directorio a monitorear")
    parser.add_argument('--api-url', type=str, default="https://api-proyecto-w9dn.onrender.com", help="URL de la API")
    args = parser.parse_args()

    # Para pruebas locales, ejecuta Flask
    app.run(host='0.0.0.0', port=5000, debug=True)
    