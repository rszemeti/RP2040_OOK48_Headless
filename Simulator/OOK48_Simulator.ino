// OOK48_Simulator.ino
// Simulates OOK48 firmware serial output for testing the Python GUI.
// Runs on an Arduino Nano (or any Arduino with USB serial).
//
// Generates a synthetic 68-bin OOK48 spectrum (495-1098Hz band) with a
// Gaussian signal peak that drifts slowly. Sends raw bin magnitudes as
// WF:<v0>,<v1>,...,<v67>  (68 values, 0-255 each)
//
// In TX mode: suppresses WF: lines, echoes TX:<char> at 9 baud (111ms/char)
//
// Behaviour:
//   - Sends RDY: on boot
//   - Accepts and ACKs all SET: and CMD: commands
//   - Sends STA: once per second with fake GPS data
//   - Sends WF: lines at ~9Hz in RX mode
//   - Sends TX:<char> at 9 baud in TX mode
//   - Occasionally sends MSG: characters to simulate decodes in RX mode

#define VERSION            "0.20-SIM"
#define NUMBEROFBINS       68       // OOK48 FFT bins (495-1098Hz)
#define WF_INTERVAL_MS     111      // ~9 lines/second
#define TX_INTERVAL_MS     999      // 9 symbols x 111ms = ~1 char/second
#define STATUS_INTERVAL_MS 1000
#define MSG_INTERVAL_MS    8000

// Fake GPS - London
#define FAKE_LAT  51.5074
#define FAKE_LON  -0.1278
#define FAKE_LOC  "IO91WM"

bool txMode   = false;
uint8_t txSlot = 0;

// TX message slots - mirrors firmware defaults
char txMessages[10][32] = {
    "G4EML IO91\r",
    "G4EML {LOC}\r",
    "EMPTY\r", "EMPTY\r", "EMPTY\r",
    "EMPTY\r", "EMPTY\r", "EMPTY\r",
    "EMPTY\r", "EMPTY\r"
};

// TX state
int  txCharIndex = 0;    // current position in active message
int  txMsgLen    = 0;    // length of active message (excluding \r)

// Signal peak in bin space
float peakBin    = 34.0;   // bin 34 = 800Hz (OOK48 tone)
float peakDrift  = 0.03;
float peakHeight = 80.0;
float peakWidth  = 2.5;
float noiseFloor = 8.0;

// Fake time
int fakeHr  = 10;
int fakeMin = 0;
int fakeSec = 0;

// Fake RX message state
const char* fakeRxMsgs[] = { "G4EML", "IO91WM", "OOK48", "TEST" };
const char* fakeRxMsg     = nullptr;
int         fakeRxIndex   = 0;
unsigned long lastRxChar  = 0;
bool        fakeRxActive  = false;

unsigned long lastWF     = 0;
unsigned long lastTxChar = 0;
unsigned long lastStatus = 0;
unsigned long lastMsg    = 0;

char    cmdBuf[128];
uint8_t cmdPtr = 0;

// ---------------------------------------------------------------------------
void setup()
{
    Serial.begin(115200);
    while (!Serial) delay(10);
    delay(500);
    Serial.print("RDY:");
    Serial.println(VERSION);
}

// ---------------------------------------------------------------------------
void loop()
{
    unsigned long now = millis();

    if (txMode)
    {
        // Echo TX characters at ~1 char/second
        if (now - lastTxChar >= TX_INTERVAL_MS)
        {
            lastTxChar = now;
            sendTxChar();
        }
    }
    else
    {
        // Waterfall runs continuously in RX mode
        if (now - lastWF >= WF_INTERVAL_MS)
        {
            lastWF = now;
            sendWaterfall();
            peakBin += peakDrift;
            if (peakBin > NUMBEROFBINS - 10 || peakBin < 10)
                peakDrift = -peakDrift;
        }

        // Fake RX message - non-blocking, one char per tick
        if (fakeRxActive && (now - lastRxChar >= TX_INTERVAL_MS))
        {
            lastRxChar = now;
            tickFakeRxMessage();
        }

        // Start a new fake message periodically
        if (!fakeRxActive && (now - lastMsg >= MSG_INTERVAL_MS))
        {
            lastMsg = now;
            startFakeMessage();
        }
    }

    // Status every second regardless of mode
    if (now - lastStatus >= STATUS_INTERVAL_MS)
    {
        lastStatus = now;
        tickTime();
        sendStatus();
    }

    // Incoming commands
    while (Serial.available())
    {
        char c = Serial.read();
        if (c == '\n' || c == '\r')
        {
            if (cmdPtr > 0)
            {
                cmdBuf[cmdPtr] = 0;
                handleCommand(cmdBuf);
                cmdPtr = 0;
            }
        }
        else
        {
            if (cmdPtr < 127) cmdBuf[cmdPtr++] = c;
        }
    }
}

// ---------------------------------------------------------------------------
void startTx()
{
    // Message length includes the terminating \r
    char* msg = txMessages[txSlot];
    txMsgLen = 0;
    while (msg[txMsgLen])
    {
        txMsgLen++;
        if (msg[txMsgLen - 1] == '\r') break;  // include CR then stop
    }
    txCharIndex = 0;
    lastTxChar  = millis() - TX_INTERVAL_MS;  // send first char immediately
    Serial.println("MRK:TX");   // purple marker on waterfall
}

void sendTxChar()
{
    if (txMsgLen == 0) return;
    char* msg = txMessages[txSlot];
    char c = msg[txCharIndex];
    Serial.print("TX:");
    Serial.println(c == '\r' ? "<CR>" : String(c));
    txCharIndex = (txCharIndex + 1) % txMsgLen;
}

// ---------------------------------------------------------------------------
void sendWaterfall()
{
    Serial.print("WF:");
    for (int b = 0; b < NUMBEROFBINS; b++)
    {
        float dist = b - peakBin;
        float val  = peakHeight * exp(-(dist * dist) / (2.0 * peakWidth * peakWidth));
        val += (random(0, 100) / 100.0) * noiseFloor;
        if (b > 0) Serial.print(',');
        Serial.print(constrain((int)val, 0, 255));
    }
    Serial.println();
}

// ---------------------------------------------------------------------------
void sendStatus()
{
    char buf[80];
    sprintf(buf, "STA:%02d:%02d:%02d,%.4f,%.4f,%s,%d",
            fakeHr, fakeMin, fakeSec,
            FAKE_LAT, FAKE_LON, FAKE_LOC,
            (int)txMode);
    Serial.println(buf);
}

// ---------------------------------------------------------------------------
void startFakeMessage()
{
    fakeRxMsg    = fakeRxMsgs[random(0, 4)];
    fakeRxIndex  = 0;
    fakeRxActive = true;
    lastRxChar   = millis() - TX_INTERVAL_MS;  // first char immediately
}

void tickFakeRxMessage()
{
    if (!fakeRxMsg) { fakeRxActive = false; return; }

    if (fakeRxMsg[fakeRxIndex])
    {
        Serial.print("MSG:");
        Serial.println(fakeRxMsg[fakeRxIndex++]);
    }
    else
    {
        // End of text - send CR
        Serial.println("MSG:<CR>");
        fakeRxActive = false;
        lastMsg = millis();   // reset interval for next message
    }
}

// ---------------------------------------------------------------------------
void tickTime()
{
    fakeSec++;
    if (fakeSec >= 60) { fakeSec = 0; fakeMin++; }
    if (fakeMin >= 60) { fakeMin = 0; fakeHr++;  }
    if (fakeHr  >= 24)   fakeHr  = 0;
}

// ---------------------------------------------------------------------------
void handleCommand(char* cmd)
{
    // SET:msg:<slot>:<text>
    if (strncmp(cmd, "SET:msg:", 8) == 0)
    {
        int slot = atoi(cmd + 8);
        if (slot >= 0 && slot <= 9)
        {
            char* text = strchr(cmd + 8, ':');
            if (text)
            {
                text++;
                strncpy(txMessages[slot], text, 30);
                txMessages[slot][30] = 0;
                int l = strlen(txMessages[slot]);
                if (l > 0 && txMessages[slot][l-1] != '\r')
                {
                    txMessages[slot][l]   = '\r';
                    txMessages[slot][l+1] = 0;
                }
            }
        }
        Serial.println("ACK:SET:msg");
        return;
    }

    // All other SET: commands - just ACK
    if (strncmp(cmd, "SET:", 4) == 0)
    {
        char key[32];
        strncpy(key, cmd, 31);
        key[31] = 0;
        char* p = strchr(key + 4, ':');
        if (p) *p = 0;
        Serial.print("ACK:");
        Serial.println(key);
        return;
    }

    if (strncmp(cmd, "CMD:txmsg:", 10) == 0)
    {
        int slot = atoi(cmd + 10);
        if (slot >= 0 && slot <= 9) txSlot = slot;
        Serial.println("ACK:CMD:txmsg");
        return;
    }

    if (strcmp(cmd, "CMD:tx") == 0)
    {
        txMode = true;
        startTx();
        Serial.println("ACK:CMD:tx");
        return;
    }

    if (strcmp(cmd, "CMD:rx") == 0)
    {
        txMode = false;
        txCharIndex = 0;
        Serial.println("MRK:RX");   // mark end of TX on waterfall
        Serial.println("ACK:CMD:rx");
        return;
    }

    if (strcmp(cmd, "CMD:clear") == 0)  { Serial.println("ACK:CMD:clear");  return; }

    if (strcmp(cmd, "CMD:reboot") == 0)
    {
        Serial.println("ACK:CMD:reboot");
        delay(200);
        txMode = false;
        txCharIndex = 0;
        Serial.print("RDY:"); Serial.println(VERSION);
        return;
    }

    Serial.print("ERR:unknown command:"); Serial.println(cmd);
}
