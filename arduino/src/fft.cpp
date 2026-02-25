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

void generatePlotData(void)
{
    float db[numberOfBins];
    float vref = 2048.0;
    static float baselevel;

    if (autolevel) baselevel = 0;

    for (int p = 0; p < numberOfBins; p++)
    {
        db[p] = 2 * (20 * (log10(magnitude[p] / vref)));
        if (autolevel) baselevel = baselevel + db[p];
    }

    if (autolevel) baselevel = baselevel / numberOfBins;

    for (int p = 0; p < numberOfBins; p++)
        db[p] = uint8_t(db[p] - baselevel);

    for (int x = 0; x < SPECWIDTH; x++)
    {
        int strtBin = (long)x * numberOfBins / SPECWIDTH;
        int endBin  = (long)(x + 1) * numberOfBins / SPECWIDTH - 1;
        if (endBin >= numberOfBins) endBin = numberOfBins - 1;
        if (endBin < 0) endBin = 0;
        uint8_t maxVal = db[strtBin];
        for (int i = strtBin + 1; i <= endBin; i++)
            if (db[i] > maxVal) maxVal = db[i];
        plotData[x] = maxVal;
    }
}

void saveCache(void)
{
    for (int i = 0; i < numberOfBins; i++)
        toneCache[i][cachePoint] = magnitude[i];
}
