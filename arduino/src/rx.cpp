#include <Arduino.h>
#include "globals.h"
#include "defines.h"
#include "dma.h"
#include "rx.h"
#include "fft.h"
#include "gui.h"
#include <float.h>

//Receive routines for an OOK48 message

void RxInit(void)
{
  sampleRate       = OVERSAMPLERATE;

  if (settings.app == MORSEMODE)
  {
    dmaTransferCount = MORSE_FRAME_SAMPLES;      // 2048 ADC samples → ~36 fps
    numberOfBins     = MORSE_FFT_BINS;
    startBin         = 0;
    rxTone           = MORSE_TONE_BIN;
    toneTolerance    = 3;
    numberOfTones    = 1;
    morseDecoder.begin(MORSE_FRAME_RATE, MORSE_MIN_WPM, MORSE_MAX_WPM, MORSE_TONE_BIN);
  }
  else
  {
    dmaTransferCount = NUMBEROFOVERSAMPLES;
    cacheSize        = CACHESIZE;
    if (halfRate) cacheSize = CACHESIZE * 2;
    rxTone           = TONE800;
    toneTolerance    = TONETOLERANCE;
    numberOfTones    = 1;
    numberOfBins     = OOKNUMBEROFBINS;
    startBin         = OOKSTARTBIN;
    calcLegend();
  }

  dma_init();                       //Initialise and start ADC conversions and DMA transfers.
  dma_handler();                    //call the interrupt handler once to start transfers
  dmaReady = false;                 //reset the transfer ready flag
  cachePoint = 0;                   //zero the data received cache
}

// Waterfall accumulator for morse mode (file-static, persists across calls)
static float  morseWfAccum[MORSE_FFT_BINS] = {};
static uint8_t morseWfCount = 0;

void RxTick(void)
{
  static unsigned long lastDma;

  if (settings.app == MORSEMODE)
  {
    if (!dmaReady) return;
    lastDma = millis();
    calcMorseSpectrum();

    // Accumulate bin magnitudes for waterfall
    for (int i = 0; i < MORSE_FFT_BINS; i++)
      morseWfAccum[i] += magnitude[i];

    // Feed morse decoder with this frame's tone bin magnitude
    int n = morseDecoder.feed(magnitude[MORSE_TONE_BIN]);
    for (int i = 0; i < n; i++)
    {
      MorseEvent ev = morseDecoder.event(i);
      if (ev.kind == MorseEvt::CHAR || ev.kind == MorseEvt::WORD_SEP)
      {
        morseDecoded = ev.ch;
        rp2040.fifo.push(MORSEMESSAGE);
      }
      else if (ev.kind == MorseEvt::LOCKED)
      {
        morseWpmEst = ev.wpm;
        rp2040.fifo.push(MORSELOCKED);
      }
      else if (ev.kind == MorseEvt::LOST)
      {
        rp2040.fifo.push(MORSELOST);
      }
    }

    // Send waterfall every MORSE_WF_FRAMES frames (~9/sec)
    if (++morseWfCount >= MORSE_WF_FRAMES)
    {
      morseWfCount = 0;
      for (int i = 0; i < MORSE_FFT_BINS; i++)
        magnitude[i] = morseWfAccum[i];
      rp2040.fifo.push(GENPLOT);
      rp2040.fifo.push(DRAWSPECTRUM);
      rp2040.fifo.push(DRAWWATERFALL);
      memset(morseWfAccum, 0, sizeof(morseWfAccum));
    }

    dmaReady = false;
    return;
  }

  // --- OOK48 path ---
  if((millis() - lastDma) > 250) cachePoint = 0;                //if we have not had a DMA transfer recently reset the pointer.

  if((dmaReady) && (cachePoint < cacheSize))                    //Do we have a complete buffer of ADC samples ready?
    {
      lastDma = millis();
      calcSpectrum();                                           //Perform the FFT of the data
      rp2040.fifo.push(GENPLOT);                                //Ask Core 1 to generate data for the Displays from the FFT results.
      rp2040.fifo.push(DRAWSPECTRUM);                           //Ask core 1 to draw the Spectrum Display
      rp2040.fifo.push(DRAWWATERFALL);                          //Ask core 1 to draw the Waterfall Display
      saveCache();                                              //save the FFT magnitudes to the cache.
      cachePoint++;
      if(cachePoint == cacheSize)                               //If the Cache is full (8 bits of data)
        {
          if(PPSActive)                                         //decodes are only valid if the PPS Pulse is present
          {
            decodeCache();                                      //extract the character
            rp2040.fifo.push(MESSAGE);                         //Ask Core 1 to display it
          }
        }
      dmaReady = false;                                         //Clear the flag ready for next time
    }
}

//search the FFT cache to find the bin containing the tone. Use the bin with the greatest max to min range 
int findBestBin(void)
{
  float max;
  float min;
  float range;
  float bestRange;
  int topBin;

  bestRange =0;
  topBin = 0;
  for(int b=rxTone - toneTolerance ; b < rxTone + toneTolerance; b++)        //search each possible bin in the search range
    {
      max = 0 - FLT_MAX;
      min = FLT_MAX;
      for(int s=0; s < cacheSize ; s++)               //search all 8 or 16 symbols in this bin to find the largest and smallest
        {
          if(toneCache[b][s] > max) max = toneCache[b][s];
          if(toneCache[b][s] < min) min = toneCache[b][s];
        }
      range = max - min;                //calculate the signal to noise for this bin
      if(range > bestRange)             //if this bin is a better choice than previous (larger signal to noise)
        {
          bestRange = range;            //make it the chosen one. 
          topBin = b;
        }

    }
  return topBin;
}

//search the magnitude cache to find the magnitude of the largest tone. 
float findLargest(int timeslot)
{
  float max;
  max = 0 - FLT_MAX;
  for(int b=rxTone - toneTolerance ; b < rxTone + toneTolerance; b++)        //search each possible bin in the search range to find the largest magnitude
    {
      if(toneCache[b][timeslot] > max) max = toneCache[b][timeslot];
    }
  return max;
}

// Sum magnitude across all OOK bins for one symbol time slot.
// Used by rainscatter mode where energy is spread across frequency.
float findWidebandPower(int timeslot)
{
  float sum = 0.0f;
  for(int b = 0; b < numberOfBins; b++)
    sum += toneCache[b][timeslot];
  return sum;
}


// Search the Tone Cache to decode the character.
// Sends SFT: soft magnitudes to GUI before hard decode.
// Applies confidence gate — low confidence chars become UNK (0x7E).
bool decodeCache(void)
{
  uint8_t dec = 0;
  float largest;
  int bestbin;
  uint8_t largestbits[4];
  float temp[CACHESIZE*2];         // working array — also used for half-rate sum
  float sorted[CACHESIZE];         // copy for confidence sort

  // Build temp[]: per-symbol max magnitude across tone bins
  if(settings.decodeMode == ALTMODE)
  {
    bestbin = findBestBin(); 
    for(int i = 0; i < cacheSize; i++)
      temp[i] = toneCache[bestbin][i];
  }
  else if(settings.decodeMode == RAINSCATTERMODE)
  {
    for(int i = 0; i < cacheSize; i++)
      temp[i] = findWidebandPower(i);
  }
  else 
  {
    for(int i = 0; i < cacheSize; i++)
      temp[i] = findLargest(i);
  }

  if(halfRate)                        // half rate: sum the two received chars into one
  {
    for(int i = 0; i < CACHESIZE; i++)
      temp[i] = temp[i] + temp[i+8];
  }


  // Copy soft magnitudes to global buffer and ask Core 1 to send SFT: line.
  // Serial must only be used from Core 1 - FIFO is the safe cross-core channel.
  // SFTMESSAGE is pushed before MESSAGE so SFT: arrives at GUI just before MSG:.
  for(int i = 0; i < CACHESIZE; i++) sftMagnitudes[i] = temp[i];
  rp2040.fifo.push(SFTMESSAGE);

  // --- Confidence: gap between rank-4 and rank-5 normalised by full range ---
  // Copy temp[] into sorted[], sort descending (8 elements, insertion sort)
  for(int i = 0; i < CACHESIZE; i++) sorted[i] = temp[i];
  for(int i = 1; i < CACHESIZE; i++)
  {
    float key = sorted[i];
    int j = i - 1;
    while(j >= 0 && sorted[j] < key) { sorted[j+1] = sorted[j]; j--; }
    sorted[j+1] = key;
  }
  float range      = sorted[0] - sorted[7];
  float confidence = (range > 0.0f) ? (sorted[3] - sorted[4]) / range : 0.0f;

  // Find the four largest magnitudes and record their bit positions
  for(int l = 0; l < 4; l++)
  {
    largest = 0;
    for(int i = 0; i < CACHESIZE; i++)
    {
      if(temp[i] > largest)
      {
        largest = temp[i];
        largestbits[l] = i;
      }
    }
    temp[largestbits[l]] = 0;
  }

  // Convert the 4 bit positions to a 4-from-8 byte
  for(int l = 0; l < 4; l++)
    dec = dec | (0x80 >> largestbits[l]);

  // --- Confidence gate ---
  // Below threshold → UNK (0x7E, shown as '~' / <UNK> in GUI).
  // Default 0.180 derived from empirical analysis; adjustable via SET:confidence: command.
  if(confidence < settings.confidenceThreshold)
  {
    decoded = 0x7E;
    return 1;
  }

  decoded = decode4from8[dec];
  return 1;
}
