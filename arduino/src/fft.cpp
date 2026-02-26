#include <arduinoFFT.h>
#include "globals.h"
#include "defines.h"
#include "fft.h"

extern ArduinoFFT<float> FFT;

void calcSpectrum(void)
{
    int bin;
    for (int i = 0; i < NUMBEROFOVERSAMPLES; i = i + OVERSAMPLE)
    {
        bin = i / OVERSAMPLE;
        sample[bin] = 0;
        for (int s = 0; s < OVERSAMPLE; s++)
            sample[bin] += buffer[bufIndex][i + s] - 2048;
        sample[bin] = sample[bin] / OVERSAMPLE;
        sampleI[bin] = 0;
    }
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
