#pragma once

#define VERSION "Version 0.25"

// GPIO Pin assignments
#define GPSTXPin    4       // Serial data to GPS module
#define GPSRXPin    5       // Serial data from GPS module
#define TXPIN       6       // Transmit output pin
#define KEYPIN      7       // Key output pin
#define PPSINPUT    3       // 1PPS signal from GPS
#define ADC_CHAN    0       // ADC2 on GPIO28 - audio input from receiver (DC biased to Vcc/2)

// OOK48 detection parameters
// Spectrum: 495Hz to 1098Hz, tone at 800Hz
#define OVERSAMPLE          8
#define NUMBEROFSAMPLES     1024
#define NUMBEROFOVERSAMPLES (NUMBEROFSAMPLES * OVERSAMPLE)
#define SAMPLERATE          9216        // 9216 sa/s → 9Hz bin spacing
#define OVERSAMPLERATE      (SAMPLERATE * OVERSAMPLE)

#define STARTFREQ           495         // First frequency of interest (Hz)
#define OOKSTARTBIN         55          // Equivalent FFT bin
#define ENDFREQ             1098        // Last frequency of interest (Hz)
#define TONE800             34          // 800Hz = 34th bin between START and END
#define TONETOLERANCE       11          // ±99Hz (11 × 9Hz bins)
#define OOKNUMBEROFBINS     68

#define CACHESIZE           8           // 8 bits per character
#define CONFIDENCE_THRESHOLD 0.180f     // below this → UNK decode (empirically derived) - default
#define TXINTERVAL          111111      // 9 symbols/second in microseconds
#define MORSE_DEFAULT_WPM   12
#define MORSE_MIN_WPM       5
#define MORSE_MAX_WPM       40
#define GPS_DEFAULT_BAUD    9600        // default GPS serial baud rate
#define LOCTOKEN            0x86        // Placeholder token for locator substitution

// JT4G detection parameters
// Spectrum: 498Hz to 1999Hz, tones at 800 1115 1430 1745Hz
#define JT4SAMPLERATE       4480
#define JT4OVERSAMPLERATE   (JT4SAMPLERATE * OVERSAMPLE)
#define JT4CACHESIZE        240
#define JT4SYMBOLCOUNT      207
#define JT4BITCOUNT         206
#define JT4HZPERBIN         4.375
#define JT4SNBINS           571.00
#define JT4STARTFREQ        498
#define JT4STARTBIN         114
#define JT4ENDFREQ          1999
#define JT4TONE0            69
#define JT4TONESPACING      72
#define JT4TONETOLERANCE    22
#define JT4NUMBEROFBINS     343

// PI4 detection parameters
// Spectrum: 498Hz to 1500Hz, tones at 683 917 1151 1385Hz
#define PI4SAMPLERATE       6144
#define PI4OVERSAMPLERATE   (PI4SAMPLERATE * OVERSAMPLE)
#define PI4CACHESIZE        180
#define PI4SYMBOLCOUNT      146
#define PI4BITCOUNT         146
#define PI4HZPERBIN         6
#define PI4SNBINS           416.00
#define PI4STARTFREQ        498
#define PI4STARTBIN         83
#define PI4ENDFREQ          1500
#define PI4TONE0            31
#define PI4TONESPACING      39
#define PI4TONETOLERANCE    12
#define PI4NUMBEROFBINS     167

#define SPECWIDTH           240

// Morse RX mode FFT / DMA parameters
// 256-pt FFT at 9216 Hz effective → 36 Hz/bin, ~36 fps
#define MORSE_FFT_SIZE        256
#define MORSE_FRAME_SAMPLES   (MORSE_FFT_SIZE * OVERSAMPLE)   // 2048 ADC samples/frame
#define MORSE_FRAME_RATE      36
#define MORSE_TONE_BIN        22    // 800 Hz / 36 Hz per bin ≈ bin 22
#define MORSE_FFT_BINS        (MORSE_FFT_SIZE / 2)            // 128 positive-freq bins
#define MORSE_WF_FRAMES       4     // accumulate N frames before waterfall send (~9/sec)
