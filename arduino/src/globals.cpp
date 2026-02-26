#include "globals.h"

struct Settings settings;
bool core0Ready = false;

uint32_t     dma_chan;
bool     dmaReady;
uint8_t  bufIndex = 0;

uint8_t  mode;
uint8_t  beaconMode;

uint32_t sampleRate;
uint16_t rxTone;
uint16_t toneTolerance;
uint16_t cacheSize;
float    hzPerBin;
uint16_t activeBins;
int      overlap = 1;
int      numberOfTones = 1;
int      numberOfBins;
int      startBin;

uint16_t toneSpacing;
uint16_t tone0;
uint16_t symbolCount;
uint16_t bitCount;
uint8_t  BeaconToneCache[JT4CACHESIZE];
char     JTmessage[14];
char     PImessage[9];
float    sigNoise;
float    snBins;
float    threshold;
float    toneCache[JT4NUMBEROFBINS][CACHESIZE * 2];
uint16_t cachePoint;
bool     halfRate = false;

char     decoded;
float    sftMagnitudes[CACHESIZE];   // soft magnitudes copied here before SFTMESSAGE push

char     gpsBuffer[256];
int      gpsPointer;
char     gpsCh;
bool     gpsActive = false;
int      lastSec = 0;
int      gpsSec = -1;
int      gpsMin = -1;
int      gpsHr = -1;
int      gpsDay = -1;
int      gpsMonth = -1;
int      gpsYear = -1;
uint8_t  PPSActive = 0;
long     lastTimeUpdate = 0;
long     lastmin;
float    latitude = 0;
float    longitude = 0;
char     qthLocator[12] = "----------";

uint16_t buffer[2][NUMBEROFOVERSAMPLES];
float    sample[NUMBEROFSAMPLES];
float    sampleI[NUMBEROFSAMPLES];
float    magnitude[JT4NUMBEROFBINS];

uint8_t  plotData[SPECWIDTH];
bool     autolevel = true;
uint16_t toneLegend[4][2];
uint8_t  audioLevel = 0;     // RX audio level 0-100

int      TxPointer = 0;
uint8_t  TxBitPointer = 0;
uint8_t  TxBuffer[50];
char     visualTxMessage[50];
uint8_t  TxMessNo;
uint8_t  TxMessLen;
bool     Key;
bool     TxSent;
char     TxCharSent;
bool     messageChanging;


// ---------------------------------------------------------------------------
// OOK48 decode lookup table
// ---------------------------------------------------------------------------
uint8_t decode4from8[256] = {0,0,0,0,0,0,0,0,0,0,
                             0,0,0,0,0,13,0,0,0,0,
                             0,0,0,32,0,0,0,33,0,34,
                             35,0,0,0,0,0,0,0,0,36,
                             0,0,0,37,0,38,39,0,0,0,
                             0,40,0,41,42,0,0,43,44,0,
                             45,0,0,0,0,0,0,0,0,0,
                             0,46,0,0,0,47,0,48,49,0,
                             0,0,0,50,0,51,52,0,0,53,
                             54,0,55,0,0,0,0,0,0,56,
                             0,57,58,0,0,59,60,0,61,0,
                             0,0,0,62,63,0,64,0,0,0,
                             65,0,0,0,0,0,0,0,0,0,
                             0,0,0,0,0,66,0,0,0,67,
                             0,68,69,0,0,0,0,70,0,71,
                             72,0,0,73,74,0,75,0,0,0,
                             0,0,0,76,0,77,78,0,0,79,
                             80,0,81,0,0,0,0,82,83,0,
                             84,0,0,0,85,0,0,0,0,0,
                             0,0,0,0,0,86,0,87,88,0,
                             0,89,90,0,91,0,0,0,0,92,
                             93,0,94,0,0,0,95,0,0,0,
                             0,0,0,0,0,126,126,0,126,0,
                             0,0,126,0,0,0,0,0,0,0,
                             126,0,0,0,0,0,0,0,0,0,
                             0,0,0,0,0,0};

// ---------------------------------------------------------------------------
// JT4 sync vector
// ---------------------------------------------------------------------------
const uint8_t JT4syncVector[JT4SYMBOLCOUNT] =
    {0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0, 0,
     0, 0, 0, 0, 1, 1, 0, 0, 0 ,0 ,0 ,0 ,0 ,0 ,0 ,0 ,0 ,0 ,1 ,0 ,1 ,1,
     0, 1, 1, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0,
     1, 1, 1, 1, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0,
     0, 1, 0, 0, 0, 1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 1, 1, 0,
     1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1,
     1, 1, 1, 0, 0, 0, 0, 1, 1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1,
     0, 1, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1,
     1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 1, 0, 1, 1,
     0, 1, 1, 1, 1, 0, 1, 0, 1};

// ---------------------------------------------------------------------------
// PI4 sync vector
// ---------------------------------------------------------------------------
const uint8_t PI4syncVector[PI4SYMBOLCOUNT] =
    {0,0,1,0,0,1,1,1,1,0,1,0,1,0,1,0,0,1,0,0,0,1,0,0,0,1,1,0,0,1,1,1,1,0,0,1,1,1,1,1,0,0,1,1,0,1,1,1,1,0,1,0,1,1,0,1,1,0,1,0,
     0,0,0,0,1,1,1,1,1,0,1,0,1,0,0,0,0,0,1,1,1,1,1,0,1,0,0,1,0,0,1,0,1,0,0,0,0,1,0,0,1,1,0,0,0,0,0,1,1,0,0,0,0,1,1,0,0,1,1,1,
     0,1,1,1,0,1,1,0,1,0,1,0,1,0,0,0,0,1,1,1,0,0,0,0,1};

// ---------------------------------------------------------------------------
// JT4 de-interleave table
// ---------------------------------------------------------------------------
const uint8_t jt4di[JT4BITCOUNT] =
{
    0x00, 0x67, 0x34, 0x9B, 0x1A, 0x81, 0x4E, 0xB5, 0x0D, 0x74, 0x41, 0xA8, 0x27, 0x8E, 0x5B, 0xC2,
    0x07, 0x6E, 0x3B, 0xA2, 0x21, 0x88, 0x55, 0xBC, 0x14, 0x7B, 0x48, 0xAF, 0x2E, 0x95, 0x61, 0xC8,
    0x04, 0x6B, 0x38, 0x9F, 0x1E, 0x85, 0x52, 0xB9, 0x11, 0x78, 0x45, 0xAC, 0x2B, 0x92, 0x5E, 0xC5,
    0x0A, 0x71, 0x3E, 0xA5, 0x24, 0x8B, 0x58, 0xBF, 0x17, 0x7E, 0x4B, 0xB2, 0x31, 0x98, 0x64, 0xCB,
    0x02, 0x69, 0x36, 0x9D, 0x1C, 0x83, 0x50, 0xB7, 0x0F, 0x76, 0x43, 0xAA, 0x29, 0x90, 0x5D, 0xC4,
    0x09, 0x70, 0x3D, 0xA4, 0x23, 0x8A, 0x57, 0xBE, 0x16, 0x7D, 0x4A, 0xB1, 0x30, 0x97, 0x63, 0xCA,
    0x06, 0x6D, 0x3A, 0xA1, 0x20, 0x87, 0x54, 0xBB, 0x13, 0x7A, 0x47, 0xAE, 0x2D, 0x94, 0x60, 0xC7,
    0x0C, 0x73, 0x40, 0xA7, 0x26, 0x8D, 0x5A, 0xC1, 0x19, 0x80, 0x4D, 0xB4, 0x33, 0x9A, 0x66, 0xCD,
    0x01, 0x68, 0x35, 0x9C, 0x1B, 0x82, 0x4F, 0xB6, 0x0E, 0x75, 0x42, 0xA9, 0x28, 0x8F, 0x5C, 0xC3,
    0x08, 0x6F, 0x3C, 0xA3, 0x22, 0x89, 0x56, 0xBD, 0x15, 0x7C, 0x49, 0xB0, 0x2F, 0x96, 0x62, 0xC9,
    0x05, 0x6C, 0x39, 0xA0, 0x1F, 0x86, 0x53, 0xBA, 0x12, 0x79, 0x46, 0xAD, 0x2C, 0x93, 0x5F, 0xC6,
    0x0B, 0x72, 0x3F, 0xA6, 0x25, 0x8C, 0x59, 0xC0, 0x18, 0x7F, 0x4C, 0xB3, 0x32, 0x99, 0x65, 0xCC,
    0x03, 0x6A, 0x37, 0x9E, 0x1D, 0x84, 0x51, 0xB8, 0x10, 0x77, 0x44, 0xAB, 0x2A, 0x91
};

// ---------------------------------------------------------------------------
// PI4 de-interleave table
// ---------------------------------------------------------------------------
const uint8_t pi4di[PI4BITCOUNT] =
{
    0,73,37,110,19,92,55,128,10,83,46,119,28,101,64,137,5,78,42,115,24,97,60,133,15,88,51,124,33,106,
    69,142,3,76,40,113,22,95,58,131,13,86,49,122,31,104,67,140,8,81,44,117,26,99,62,135,17,90,53,126,
    35,108,71,144,2,75,39,112,21,94,57,130,12,85,48,121,30,103,66,139,7,80,43,116,25,98,61,134,16,89,
    52,125,34,107,70,143,4,77,41,114,23,96,59,132,14,87,50,123,32,105,68,141,9,82,45,118,27,100,63,136,
    18,91,54,127,36,109,72,145,1,74,38,111,20,93,56,129,11,84,47,120,29,102,65,138,6,79
};
