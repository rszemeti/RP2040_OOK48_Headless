// gui.cpp - LCD removed. generatePlotData() retained for WF: serial output.
// calcLegend() retained as it sets toneLegend[] used by beacon init.
#include <Arduino.h>
#include "globals.h"
#include "defines.h"
#include "gui.h"

// Generate the plot data array from FFT magnitudes with log scaling.
// Results go into plotData[] and are sent as WF: lines via serial.
void generatePlotData(void)
{
    float db[numberOfBins];
    float vref = 2048.0;
    static float baselevel;

    if (autolevel) baselevel = 0;

    for (int p = 0; p < numberOfBins; p++)
    {
        db[p] = 2 * (20 * (log10(magnitude[p] / vref)));
        if (autolevel) baselevel += db[p];
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

void calcLegend(void)
{
    if (settings.app == OOK48 || settings.app == MORSEMODE)
    {
        toneLegend[0][0] = (rxTone - toneTolerance) * SPECWIDTH / numberOfBins;
        toneLegend[0][1] = (toneTolerance * 2) * SPECWIDTH / numberOfBins;
    }
    else
    {
        for (int t = 0; t < numberOfTones; t++)
        {
            toneLegend[t][0] = (tone0 + (toneSpacing * t) - toneTolerance) * SPECWIDTH / numberOfBins;
            toneLegend[t][1] = (toneTolerance * 2) * SPECWIDTH / numberOfBins;
        }
    }
}
