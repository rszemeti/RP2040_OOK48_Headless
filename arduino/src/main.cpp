// OOK48 Encoder and Decoder - Serial Control Version
// LCD retained for Spectrum/Waterfall display only.
// All control and decode output via USB Serial (115200 baud).
// Configuration supplied by Python GUI on connect.
// Colin Durbridge G4EML 2025 - Modified for Serial control / PlatformIO

#include <Arduino.h>
#include <hardware/dma.h>
#include <hardware/adc.h>
#include "hardware/irq.h"
#include <arduinoFFT.h>
#include <TFT_eSPI.h>
#include "defines.h"
#include "globals.h"
#include "dma.h"
#include "fft.h"
#include "rx.h"
#include "tx.h"
#include "gui.h"
#include "beacon.h"

// ---------------------------------------------------------------------------
// Serial protocol
// Firmware sends:
//   RDY:<version>                              on boot, ready for config push
//   STA:<hh>:<mm>:<ss>,<lat>,<lon>,<loc>,<tx>  status once per second
//   MSG:<char>                                 OOK48 decoded character
//   ERR:<char>                                 OOK48 decode error character
//   TX:<char>                                  OOK48 transmitted character echo
//   JT:<hh>:<mm>,<snr>,<message>               JT4 decoded message
//   PI:<hh>:<mm>,<snr>,<message>               PI4 decoded message
//   ACK:<command>  /  ERR:<reason>             command response
//
// Firmware accepts (newline terminated):
//   SET:gpsbaud:<9600|38400>
//   SET:loclen:<6|8|10>
//   SET:decmode:<0|1>
//   SET:txadv:<0-999>
//   SET:rxret:<0-999>
//   SET:halfrate:<0|1>
//   SET:app:<0|1|2>        0=OOK48 1=JT4 2=PI4  (causes reboot)
//   SET:msg:<0-9>:<text>   set TX message slot
//   CMD:tx                 switch to transmit
//   CMD:rx                 switch to receive
//   CMD:txmsg:<0-9>        select TX message slot
//   CMD:clear              no-op, ack only
//   CMD:reboot             reboot device
// ---------------------------------------------------------------------------

// Shared objects - declared here, extern'd via headers in each module
TFT_eSPI tft = TFT_eSPI();
ArduinoFFT<float> FFT = ArduinoFFT<float>(sample, sampleI, NUMBEROFSAMPLES, SAMPLERATE);

struct repeating_timer TxIntervalTimer;
struct repeating_timer PPSIntervalTimer;

// ---------------------------------------------------------------------------
// Forward declarations for functions defined in this file
// ---------------------------------------------------------------------------
void defaultSettings(void);
void sendStatus(void);
void processSerial(void);
void handleCommand(char *cmd);
void processNMEA(void);
bool RMCValid(void);
float convertToDecimalDegrees(float dddmm_mmm);
void convertToMaid(void);
void replaceToken(char *news, char *orig, char search, const char *rep);
bool checksum(const char *sentence);

// ---------------------------------------------------------------------------
// Core 0 setup - time critical radio work
// ---------------------------------------------------------------------------
void setup()
{
    Serial.begin(115200);
    defaultSettings();

    pinMode(PPSINPUT, INPUT);
    pinMode(KEYPIN, OUTPUT);
    digitalWrite(KEYPIN, 0);
    pinMode(TXPIN, OUTPUT);
    digitalWrite(TXPIN, 0);

    if (settings.app == OOK48)
    {
        mode = RX;
        RxInit();
        TxMessNo = 0;
        TxInit();
    }
    else
    {
        if (settings.app == BEACONPI4)
        {
            beaconMode = PI4;
            PI4Init();
        }
        else
        {
            beaconMode = JT4;
            JT4Init();
        }
    }
    attachInterrupt(PPSINPUT, ppsISR, RISING);
}

bool TxIntervalInterrupt(struct repeating_timer *t)
{
    TxSymbol();
    return true;
}

bool PPSIntervalInterrupt(struct repeating_timer *t)
{
    doPPS();
    return false;
}

void ppsISR(void)
{
    if (mode == RX)
    {
        if (settings.rxRetard == 0)
            doPPS();
        else
            add_repeating_timer_ms(settings.rxRetard, PPSIntervalInterrupt, NULL, &PPSIntervalTimer);
    }
    else
    {
        if (settings.txAdvance == 0)
            doPPS();
        else
            add_repeating_timer_ms(1000 - settings.txAdvance, PPSIntervalInterrupt, NULL, &PPSIntervalTimer);
    }
}

void doPPS(void)
{
    PPSActive = 3;
    if (settings.app == OOK48)
    {
        if (mode == RX)
        {
            dma_stop();
            dma_handler();
            dmaReady = 0;
            if ((halfRate == false) || (halfRate & (gpsSec & 0x01)))
                cachePoint = 0;
            else
                cachePoint = 8;
        }
        else
        {
            cancel_repeating_timer(&TxIntervalTimer);
            add_repeating_timer_us(-TXINTERVAL, TxIntervalInterrupt, NULL, &TxIntervalTimer);
            TxSymbol();
        }
    }
}

void loop()
{
    if (settings.app == OOK48)
    {
        if (mode == RX)
            RxTick();
        else
            TxTick();
    }
    else
    {
        beaconTick();
    }
}

// ---------------------------------------------------------------------------
// Core 1 - LCD (spectrum/waterfall) + GPS + Serial comms
// ---------------------------------------------------------------------------
void setup1()
{
    Serial2.setRX(GPSRXPin);
    Serial2.setTX(GPSTXPin);

    while (!core0Ready) delay(1);

    Serial2.begin(settings.gpsBaud);

    gpsPointer = 0;
    waterRow = 0;

    tft.init();
    tft.setRotation(1);
    tft.fillScreen(TFT_BLACK);
    touch_calibrate_silent();
    clearSpectrum();
    drawLegend();

    Serial.print("RDY:");
    Serial.println(VERSION);
}

void loop1()
{
    uint32_t command;
    char m[64];

    if ((gpsSec != lastSec) || (millis() > lastTimeUpdate + 2000))
    {
        sendStatus();
        if (PPSActive > 0) PPSActive--;
        lastSec = gpsSec;
        lastTimeUpdate = millis();
    }

    if (rp2040.fifo.pop_nb(&command))
    {
        switch (command)
        {
        case GENPLOT:
            generatePlotData();
            break;
        case DRAWSPECTRUM:
            drawSpectrum();
            break;
        case DRAWWATERFALL:
            drawWaterfall();
            break;
        case REDLINE:
            markWaterfall(TFT_RED);
            break;
        case CYANLINE:
            markWaterfall(TFT_CYAN);
            break;
        case MESSAGE:
            Serial.print("MSG:");
            Serial.println(decoded);
            break;
        case TMESSAGE:
            Serial.print("TX:");
            Serial.println(TxCharSent);
            break;
        case ERROR:
            Serial.print("ERR:");
            Serial.println(decoded);
            break;
        case JTMESSAGE:
            sprintf(m, "JT:%02d:%02d,%.0f,%s", gpsHr, gpsMin, sigNoise, JTmessage);
            Serial.println(m);
            break;
        case PIMESSAGE:
            sprintf(m, "PI:%02d:%02d,%.0f,%s", gpsHr, gpsMin, sigNoise, PImessage);
            Serial.println(m);
            break;
        }
    }

    processSerial();

    if (Serial2.available() > 0)
    {
        while (Serial2.available() > 0)
        {
            gpsCh = Serial2.read();
            if (gpsCh > 31) gpsBuffer[gpsPointer++] = gpsCh;
            if ((gpsCh == 13) || (gpsPointer > 255))
            {
                gpsBuffer[gpsPointer] = 0;
                processNMEA();
                gpsPointer = 0;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Serial command handler
// ---------------------------------------------------------------------------
char serialBuf[128];
uint8_t serialPtr = 0;

void processSerial(void)
{
    while (Serial.available())
    {
        char c = Serial.read();
        if (c == '\n' || c == '\r')
        {
            if (serialPtr > 0)
            {
                serialBuf[serialPtr] = 0;
                handleCommand(serialBuf);
                serialPtr = 0;
            }
        }
        else
        {
            if (serialPtr < 127) serialBuf[serialPtr++] = c;
        }
    }
}

void handleCommand(char *cmd)
{
    if (strncmp(cmd, "SET:gpsbaud:", 12) == 0)
    {
        int b = atoi(cmd + 12);
        if (b == 9600 || b == 38400)
        {
            settings.gpsBaud = b;
            Serial2.end();
            Serial2.begin(settings.gpsBaud);
            Serial.println("ACK:SET:gpsbaud");
        }
        else Serial.println("ERR:invalid baud rate");
        return;
    }

    if (strncmp(cmd, "SET:loclen:", 11) == 0)
    {
        int l = atoi(cmd + 11);
        if (l == 6 || l == 8 || l == 10)
        {
            settings.locatorLength = l;
            qthLocator[l] = '\0';
            Serial.println("ACK:SET:loclen");
        }
        else Serial.println("ERR:invalid locator length");
        return;
    }

    if (strncmp(cmd, "SET:decmode:", 12) == 0)
    {
        int d = atoi(cmd + 12);
        if (d == 0 || d == 1)
        {
            settings.decodeMode = d;
            Serial.println("ACK:SET:decmode");
        }
        else Serial.println("ERR:invalid decode mode");
        return;
    }

    if (strncmp(cmd, "SET:txadv:", 10) == 0)
    {
        int v = atoi(cmd + 10);
        if (v >= 0 && v <= 999)
        {
            settings.txAdvance = v;
            Serial.println("ACK:SET:txadv");
        }
        else Serial.println("ERR:value out of range");
        return;
    }

    if (strncmp(cmd, "SET:rxret:", 10) == 0)
    {
        int v = atoi(cmd + 10);
        if (v >= 0 && v <= 999)
        {
            settings.rxRetard = v;
            Serial.println("ACK:SET:rxret");
        }
        else Serial.println("ERR:value out of range");
        return;
    }

    if (strncmp(cmd, "SET:halfrate:", 13) == 0)
    {
        int v = atoi(cmd + 13);
        halfRate = (v != 0);
        cacheSize = halfRate ? CACHESIZE * 2 : CACHESIZE;
        Serial.println("ACK:SET:halfrate");
        return;
    }

    if (strncmp(cmd, "SET:app:", 8) == 0)
    {
        int v = atoi(cmd + 8);
        if (v >= 0 && v <= 2)
        {
            settings.app = v;
            Serial.println("ACK:SET:app - rebooting");
            delay(100);
            rp2040.reboot();
        }
        else Serial.println("ERR:invalid app");
        return;
    }

    if (strncmp(cmd, "SET:msg:", 8) == 0)
    {
        int slot = atoi(cmd + 8);
        if (slot >= 0 && slot <= 9)
        {
            char *text = strchr(cmd + 8, ':');
            if (text)
            {
                text++;
                strncpy(settings.TxMessage[slot], text, 30);
                settings.TxMessage[slot][30] = 0;
                int l = strlen(settings.TxMessage[slot]);
                if (l > 0 && settings.TxMessage[slot][l - 1] != '\r')
                {
                    settings.TxMessage[slot][l] = '\r';
                    settings.TxMessage[slot][l + 1] = 0;
                }
                Serial.println("ACK:SET:msg");
            }
            else Serial.println("ERR:missing text");
        }
        else Serial.println("ERR:invalid slot");
        return;
    }

    if (strcmp(cmd, "CMD:tx") == 0)
    {
        if (settings.app == OOK48 && mode == RX)
        {
            mode = TX;
            TxInit();
            digitalWrite(TXPIN, 1);
            TxPointer = 0;
            TxBitPointer = 0;
            tft.fillRect(SPECLEFT, SPECTOP, SPECWIDTH, SPECHEIGHT + WATERHEIGHT + LEGHEIGHT, TFT_RED);
            tft.setTextColor(TFT_BLACK);
            tft.setFreeFont(&FreeSansBold24pt7b);
            tft.setTextDatum(TL_DATUM);
            tft.drawString("TX", SPECLEFT + (SPECWIDTH) / 2 - 40, SPECTOP + SPECHEIGHT);
            Serial.println("ACK:CMD:tx");
        }
        else Serial.println("ERR:not in OOK48 RX mode");
        return;
    }

    if (strcmp(cmd, "CMD:rx") == 0)
    {
        if (mode == TX)
        {
            mode = RX;
            digitalWrite(KEYPIN, 0);
            digitalWrite(TXPIN, 0);
            cancel_repeating_timer(&TxIntervalTimer);
            clearSpectrum();
            drawLegend();
            waterRow = 0;
            Serial.println("ACK:CMD:rx");
        }
        else Serial.println("ACK:CMD:rx - already RX");
        return;
    }

    if (strncmp(cmd, "CMD:txmsg:", 10) == 0)
    {
        int slot = atoi(cmd + 10);
        if (slot >= 0 && slot <= 9)
        {
            TxMessNo = slot;
            messageChanging = true;
            if (mode == TX)
            {
                cancel_repeating_timer(&TxIntervalTimer);
                TxInit();
            }
            messageChanging = false;
            Serial.println("ACK:CMD:txmsg");
        }
        else Serial.println("ERR:invalid slot");
        return;
    }

    if (strcmp(cmd, "CMD:clear") == 0)
    {
        Serial.println("ACK:CMD:clear");
        return;
    }

    if (strcmp(cmd, "CMD:reboot") == 0)
    {
        Serial.println("ACK:CMD:reboot");
        delay(100);
        rp2040.reboot();
        return;
    }

    Serial.print("ERR:unknown command:");
    Serial.println(cmd);
}

// ---------------------------------------------------------------------------
// Status line
// ---------------------------------------------------------------------------
void sendStatus(void)
{
    char s[64];
    if (PPSActive > 0 && gpsSec != -1)
        sprintf(s, "STA:%02d:%02d:%02d,%.4f,%.4f,%s,%d",
                gpsHr, gpsMin, gpsSec, latitude, longitude, qthLocator, (int)(mode == TX));
    else
        sprintf(s, "STA:--:--:--,0,0,----------,%d", (int)(mode == TX));
    Serial.println(s);
}

// ---------------------------------------------------------------------------
// Settings defaults
// ---------------------------------------------------------------------------
void defaultSettings(void)
{
    settings.gpsBaud       = 9600;
    settings.baudMagic     = 42;
    settings.locatorLength = 8;
    settings.decodeMode    = 0;
    settings.txAdvance     = 0;
    settings.rxRetard      = 0;
    settings.app           = OOK48;
    settings.calMagic      = 0;
    for (int i = 0; i < 10; i++)
        strcpy(settings.TxMessage[i], "EMPTY\r");
    core0Ready = true;
}

// ---------------------------------------------------------------------------
// GPS / NMEA processing
// ---------------------------------------------------------------------------
void processNMEA(void)
{
    float gpsTime, gpsDate;
    gpsActive = true;
    if (RMCValid())
    {
        int p = strcspn(gpsBuffer, ",") + 1;
        p = p + strcspn(gpsBuffer + p, ",") + 1;
        if (gpsBuffer[p] == 'A')
        {
            p = strcspn(gpsBuffer, ",") + 1;
            gpsTime = strtof(gpsBuffer + p, NULL);
            gpsSec = int(gpsTime) % 100; gpsTime /= 100;
            gpsMin = int(gpsTime) % 100; gpsTime /= 100;
            gpsHr  = int(gpsTime) % 100;

            p = p + strcspn(gpsBuffer + p, ",") + 1;
            p = p + strcspn(gpsBuffer + p, ",") + 1;
            latitude = convertToDecimalDegrees(strtof(gpsBuffer + p, NULL));
            p = p + strcspn(gpsBuffer + p, ",") + 1;
            if (gpsBuffer[p] == 'S') latitude = -latitude;
            p = p + strcspn(gpsBuffer + p, ",") + 1;
            longitude = convertToDecimalDegrees(strtof(gpsBuffer + p, NULL));
            p = p + strcspn(gpsBuffer + p, ",") + 1;
            if (gpsBuffer[p] == 'W') longitude = -longitude;
            p = p + strcspn(gpsBuffer + p, ",") + 1;
            p = p + strcspn(gpsBuffer + p, ",") + 1;
            p = p + strcspn(gpsBuffer + p, ",") + 1;

            gpsDate  = strtof(gpsBuffer + p, NULL);
            gpsYear  = int(gpsDate) % 100; gpsDate /= 100;
            gpsMonth = int(gpsDate) % 100; gpsDate /= 100;
            gpsDay   = int(gpsDate) % 100;

            convertToMaid();
        }
        else
        {
            gpsSec = gpsMin = gpsHr = -1;
            latitude = longitude = 0;
            strcpy(qthLocator, "----------");
            qthLocator[settings.locatorLength] = '\0';
        }
    }
}

bool RMCValid(void)
{
    if ((gpsBuffer[3] == 'R') && (gpsBuffer[4] == 'M') && (gpsBuffer[5] == 'C'))
        return checksum(gpsBuffer);
    return false;
}

float convertToDecimalDegrees(float dddmm_mmm)
{
    int degrees = (int)(dddmm_mmm / 100);
    float minutes = dddmm_mmm - (degrees * 100);
    return degrees + (minutes / 60.0);
}

void convertToMaid(void)
{
    float d = 0.5 * (180.0 + longitude);
    int ii = (int)(0.1 * d);
    qthLocator[0] = char(ii + 65);
    float rj = d - 10.0 * ii; int j = (int)rj;
    qthLocator[2] = char(j + 48);
    float rk = 24.0 * (rj - j);  int k = (int)rk;
    qthLocator[4] = char(k + 65);
    float rl = 10.0 * (rk - k);  int l = (int)rl;
    qthLocator[6] = char(l + 48);
    float rm = 24.0 * (rl - l);  int mm = (int)rm;
    qthLocator[8] = char(mm + 65);

    d = 90.0 + latitude;
    ii = (int)(0.1 * d);
    qthLocator[1] = char(ii + 65);
    rj = d - 10.0 * ii; j = (int)rj;
    qthLocator[3] = char(j + 48);
    rk = 24.0 * (rj - j); k = (int)rk;
    qthLocator[5] = char(k + 65);
    rl = 10.0 * (rk - k); l = (int)rl;
    qthLocator[7] = char(l + 48);
    rm = 24.0 * (rl - l); mm = (int)rm;
    qthLocator[9] = char(mm + 65);
    qthLocator[settings.locatorLength] = '\0';
}

void replaceToken(char *news, char *orig, char search, const char *rep)
{
    int outp = 0;
    for (int i = 0;;i++)
    {
        if (orig[i] == search)
        {
            for (int q = 0;;q++)
            {
                if (rep[q] == 0) break;
                news[outp++] = rep[q];
            }
        }
        else news[outp++] = orig[i];
        if (orig[i] == 0) break;
    }
}

bool checksum(const char *sentence)
{
    if (sentence == NULL || sentence[0] != '$') return false;
    const char *cs = strchr(sentence, '*');
    if (cs == NULL || strlen(cs) < 3) return false;
    unsigned char calc = 0;
    for (const char *p = sentence + 1; p < cs; ++p) calc ^= (unsigned char)(*p);
    unsigned int provided = 0;
    if (sscanf(cs + 1, "%2x", &provided) != 1) return false;
    return calc == (unsigned char)provided;
}
