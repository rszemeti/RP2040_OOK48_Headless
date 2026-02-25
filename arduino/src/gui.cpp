#include <TFT_eSPI.h>
#include "globals.h"
#include "defines.h"
#include "gui.h"

extern TFT_eSPI tft;

void clearSpectrum(void)
{
    tft.fillRect(SPECLEFT, SPECHEIGHT, SPECWIDTH, WATERHEIGHT + LEGHEIGHT, TFT_BLACK);
    tft.fillRect(SPECLEFT, SPECTOP, SPECWIDTH, SPECHEIGHT, TFT_CYAN);
}

void drawWaterfall(void)
{
    if (mode == RX)
    {
        if (waterRow < WATERHEIGHT - 1)
            tft.drawFastHLine(WATERLEFT, WATERTOP + waterRow + 1, WATERWIDTH, TFT_WHITE);
        for (int p = 0; p < WATERWIDTH; p++)
            tft.drawPixel(WATERLEFT + p, WATERTOP + waterRow, waterColours[(plotData[p] + 10) * 2]);
        waterRow++;
        if (waterRow >= WATERHEIGHT) waterRow = 0;
    }
}

void markWaterfall(unsigned int col)
{
    if (mode == RX)
        tft.drawFastHLine(WATERLEFT, WATERTOP + waterRow - 1, WATERWIDTH, col);
}

void drawSpectrum(void)
{
    if (mode == RX)
    {
        for (int p = 1; p < SPECWIDTH; p++)
        {
            tft.drawLine(SPECLEFT + p - 1, SPECTOP + SPECHEIGHT - lastplotData[p - 1],
                         SPECLEFT + p,     SPECTOP + SPECHEIGHT - lastplotData[p], TFT_CYAN);
            tft.drawLine(SPECLEFT + p - 1, SPECTOP + SPECHEIGHT - plotData[p - 1],
                         SPECLEFT + p,     SPECTOP + SPECHEIGHT - plotData[p], TFT_RED);
        }
        memcpy(lastplotData, plotData, SPECWIDTH);
    }
}

void drawLegend(void)
{
    tft.fillRect(LEGLEFT, LEGTOP, LEGWIDTH, LEGHEIGHT, TFT_WHITE);
    for (int l = 0; l < numberOfTones; l++)
        tft.fillRect(toneLegend[l][0], LEGTOP, 1 + toneLegend[l][1], LEGHEIGHT, TFT_ORANGE);
}

void calcLegend(void)
{
    if (settings.app == OOK48)
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

void touch_calibrate_silent(void)
{
    if (settings.calMagic == 0x0A)
        tft.setTouch(settings.calData);
}
