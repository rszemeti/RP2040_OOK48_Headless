#include <arduinoFFT.h>
#include "globals.h"
#include "defines.h"
#include "fft.h"

extern ArduinoFFT<float> FFT;

void calcSpectrum(void)
{
    int bin;
    float peak = 0.0f;
    for (int i = 0; i < NUMBEROFOVERSAMPLES; i = i + OVERSAMPLE)
    {
        bin = i / OVERSAMPLE;
        sample[bin] = 0;
        for (int s = 0; s < OVERSAMPLE; s++)
            sample[bin] += buffer[bufIndex][i + s] - 2048;
        sample[bin] = sample[bin] / OVERSAMPLE;
        sampleI[bin] = 0;
        float a = sample[bin] < 0 ? -sample[bin] : sample[bin];
        if (a > peak) peak = a;
    }
    // Smooth peak into audioLevel (EMA alpha≈0.4), scale 2048 → 100
    uint8_t newLevel = (uint8_t)(peak / 2048.0f * 100.0f < 100.0f ? peak / 2048.0f * 100.0f : 100.0f);
    audioLevel = (uint8_t)(audioLevel * 0.6f + newLevel * 0.4f);
    FFT.windowing(FFTWindow::Hann, FFTDirection::Forward);
    FFT.compute(FFTDirection::Forward);
    FFT.complexToMagnitude();
    for (int m = 0; m < numberOfBins; m++)
        magnitude[m] = sample[startBin + m];
}

void saveCache(void)
{
    for (int i = 0; i < numberOfBins; i++)
        toneCache[i][cachePoint] = magnitude[i];
}

// ---------------------------------------------------------------------------
// Morse-mode spectrum  — 256-pt FFT, ~36 fps
// Processes MORSE_FRAME_SAMPLES ADC samples (2048) → 256 effective samples.
// Writes MORSE_FFT_BINS (128) magnitudes into magnitude[].
// ---------------------------------------------------------------------------
static ArduinoFFT<float> MorseFFT(sample, sampleI, MORSE_FFT_SIZE, (float)SAMPLERATE);

void calcMorseSpectrum(void)
{
    float peak = 0.0f;
    for (int i = 0; i < MORSE_FRAME_SAMPLES; i += OVERSAMPLE)
    {
        int bin = i / OVERSAMPLE;
        sample[bin] = 0;
        for (int s = 0; s < OVERSAMPLE; s++)
            sample[bin] += (float)buffer[bufIndex][i + s] - 2048.0f;
        sample[bin] /= (float)OVERSAMPLE;
        sampleI[bin] = 0.0f;
        float a = sample[bin] < 0 ? -sample[bin] : sample[bin];
        if (a > peak) peak = a;
    }

    uint8_t newLevel = (uint8_t)(peak / 2048.0f * 100.0f < 100.0f ? peak / 2048.0f * 100.0f : 100.0f);
    audioLevel = (uint8_t)(audioLevel * 0.6f + newLevel * 0.4f);

    MorseFFT.windowing(FFTWindow::Hann, FFTDirection::Forward);
    MorseFFT.compute(FFTDirection::Forward);
    MorseFFT.complexToMagnitude();

    for (int m = 0; m < MORSE_FFT_BINS; m++)
        magnitude[m] = sample[m];
}
