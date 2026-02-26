#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "defines.h"

// ---------------------------------------------------------------------------
// Settings structure - held in RAM, pushed from Python GUI on connect.
// No EEPROM in this version.
// ---------------------------------------------------------------------------
struct Settings
{
    uint8_t  calMagic;          // touch cal magic (kept for potential future use)
    uint16_t calData[5];        // touch calibration data
    uint8_t  baudMagic;         // set to 42 by defaultSettings() so core1 wait loop exits
    uint8_t  messageMagic;      // unused, kept for struct layout compatibility
    char     TxMessage[10][32];
    uint8_t  locatorLength;
    uint8_t  decodeMode;
    uint16_t txAdvance;
    uint16_t rxRetard;
    uint8_t  app;
    float    confidenceThreshold;   // OOK48 decode confidence gate (default CONFIDENCE_THRESHOLD)
};

extern struct Settings settings;
extern bool core0Ready;

// ---------------------------------------------------------------------------
// Enumerations
// ---------------------------------------------------------------------------
enum DecodeModes  { NORMALMODE, ALTMODE, RAINSCATTERMODE };
enum Core1Message { GENPLOT, DRAWSPECTRUM, DRAWWATERFALL, REDLINE, CYANLINE,
                    MESSAGE, TMESSAGE, ERROR, JTMESSAGE, PIMESSAGE, SFTMESSAGE };
enum Apps         { OOK48, BEACONJT4, BEACONPI4 };
enum Modes        { RX, TX };
enum BModes       { JT4, PI4 };

// ---------------------------------------------------------------------------
// Global variables - defined in globals.cpp
// ---------------------------------------------------------------------------
extern uint32_t     dma_chan;
extern bool     dmaReady;
extern uint8_t  bufIndex;

extern uint8_t  mode;
extern uint8_t  beaconMode;

extern uint32_t sampleRate;
extern uint16_t rxTone;
extern uint16_t toneTolerance;
extern uint16_t cacheSize;
extern float    hzPerBin;
extern uint16_t activeBins;
extern int      overlap;
extern int      numberOfTones;
extern int      numberOfBins;
extern int      startBin;

// Beacon
extern uint16_t toneSpacing;
extern uint16_t tone0;
extern uint16_t symbolCount;
extern uint16_t bitCount;
extern uint8_t  BeaconToneCache[JT4CACHESIZE];
extern char     JTmessage[14];
extern char     PImessage[9];
extern float    sigNoise;
extern float    snBins;
extern float    threshold;
extern float    toneCache[JT4NUMBEROFBINS][CACHESIZE * 2];
extern uint16_t cachePoint;
extern bool     halfRate;

extern char     decoded;
extern float    sftMagnitudes[CACHESIZE];  // soft magnitudes for SFT: serial output

// GPS
extern char     gpsBuffer[256];
extern int      gpsPointer;
extern char     gpsCh;
extern bool     gpsActive;
extern int      lastSec;
extern int      gpsSec;
extern int      gpsMin;
extern int      gpsHr;
extern int      gpsDay;
extern int      gpsMonth;
extern int      gpsYear;
extern uint8_t  PPSActive;
extern long     lastTimeUpdate;
extern long     lastmin;
extern float    latitude;
extern float    longitude;
extern char     qthLocator[12];

// ADC / FFT buffers
extern uint16_t buffer[2][NUMBEROFOVERSAMPLES];
extern float    sample[NUMBEROFSAMPLES];
extern float    sampleI[NUMBEROFSAMPLES];
extern float    magnitude[JT4NUMBEROFBINS];
extern uint8_t  audioLevel;    // RX audio level 0-100 (peak-smoothed, for volume guidance)

// Display
extern uint8_t  plotData[SPECWIDTH];
extern bool     autolevel;
extern uint16_t toneLegend[4][2];

// TX
extern int      TxPointer;
extern uint8_t  TxBitPointer;
extern uint8_t  TxBuffer[50];
extern char     visualTxMessage[50];
extern uint8_t  TxMessNo;
extern uint8_t  TxMessLen;
extern bool     Key;
extern bool     TxSent;
extern char     TxCharSent;
extern bool     messageChanging;

// ---------------------------------------------------------------------------
// Lookup tables - defined in globals.cpp
// ---------------------------------------------------------------------------
extern uint8_t  decode4from8[256];
extern const uint8_t JT4syncVector[JT4SYMBOLCOUNT];
extern const uint8_t PI4syncVector[PI4SYMBOLCOUNT];
extern const uint8_t jt4di[JT4BITCOUNT];
extern const uint8_t pi4di[PI4BITCOUNT];
