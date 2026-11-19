#include "esp_camera.h"

// --- PINOS INTERNOS DA CÂMERA (Freenove ESP32-S3-CAM) ---
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  15
#define SIOD_GPIO_NUM  4
#define SIOC_GPIO_NUM  5
#define Y9_GPIO_NUM    16
#define Y8_GPIO_NUM    17
#define Y7_GPIO_NUM    18
#define Y6_GPIO_NUM    12
#define Y5_GPIO_NUM    10
#define Y4_GPIO_NUM    8
#define Y3_GPIO_NUM    9
#define Y2_GPIO_NUM    11
#define VSYNC_GPIO_NUM 6
#define HREF_GPIO_NUM  7
#define PCLK_GPIO_NUM  13

// --- PINOS DO BOTÃO E LED ---
const int btnEntradaPin = 42; // Botão de Entrada (conectado ao GND)
const int ledPin        = 2;  // LED indicador da placa
const int buzzerPin = 21; // Defina o pino onde conectou o positivo (S) do buzzer ativo
// --- DIRETÓRIO ONDE A FOTO FICARÁ ARMAZENADA NO NOTEBOOK ---
// Ajuste este caminho se a pasta do projeto for movida.
const char* CAMINHO_FOTO_NOTEBOOK = "/home/juliolacerda/jj/despensa/imagens/foto.jpg";

unsigned long tempoUltimoClique = 0;
bool travaLeitura = false; // Variável global para travar a câmera
void configurarCamera3MP() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;

  // Qualidade máxima da câmera 3MP (HDF3M-811 xR)
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = FRAMESIZE_VGA;    // 2048x1536 (3MP)
  config.jpeg_quality = 60;                 // Qualidade máxima (menor número = menor compressão)
  config.fb_location  = CAMERA_FB_IN_PSRAM;
  config.grab_mode    = CAMERA_GRAB_LATEST;
  config.fb_count     = 2;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("ERRO na camera: 0x%x\n", err);
    return;
  }

  sensor_t * s = esp_camera_sensor_get();
  if (s) {
    s->set_quality(s, 4);
    s->set_sharpness(s, 2);
  }
}

void tirarFoto() {
  digitalWrite(ledPin, HIGH);

  // Limpa o buffer antigo e captura a foto do momento exato do clique
  camera_fb_t * fb = esp_camera_fb_get();
  if (fb) esp_camera_fb_return(fb);

  fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("ERRO: Falha ao capturar a imagem.");
    digitalWrite(ledPin, LOW);
    return;
  }

  // Envia o cabeçalho com o diretório de destino e o tamanho da foto via cabo USB
  Serial.printf("\nINICIO_FOTO|%s|%u\n", CAMINHO_FOTO_NOTEBOOK, fb->len);
  
  // Transfere os bytes da imagem JPG diretamente pela porta USB
  Serial.write(fb->buf, fb->len);
  
  Serial.println("\nFIM_FOTO");

  esp_camera_fb_return(fb);
  digitalWrite(ledPin, LOW);
}

void setup() {
  pinMode(buzzerPin, OUTPUT);
  digitalWrite(buzzerPin, LOW); // Garante que o buzzer comece desligado
  Serial.begin(921600); // Velocidade alta para transferir a foto de 3MP rápido via USB
  pinMode(btnEntradaPin, INPUT_PULLUP);
  pinMode(ledPin, OUTPUT);
  digitalWrite(ledPin, LOW);

  configurarCamera3MP();
  Serial.println("Camera 3MP pronta! Pressione o botao no pino 42.");
}

void loop() {
  if (Serial.available() > 0) {
    String comando = Serial.readStringUntil('\n');
    comando.trim();
    
    if (comando == "LIDO") {
      travaLeitura = true;
      // Bipe curto e agudo avisando que a câmera capturou o código
      digitalWrite(buzzerPin, HIGH);
      delay(50); 
      digitalWrite(buzzerPin, LOW);
    }
    else if (comando == "CADASTRADO") {
      // Bipe longo avisando que o produto foi salvo no banco de dados
      digitalWrite(buzzerPin, HIGH);
      delay(400); 
      digitalWrite(buzzerPin, LOW);
    }
  }

  if (digitalRead(btnEntradaPin) == HIGH) {
    travaLeitura = false; 
  } 
  else if (digitalRead(btnEntradaPin) == LOW && !travaLeitura) {
    tirarFoto();
    delay(200);
  }
}