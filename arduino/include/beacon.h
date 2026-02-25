#pragma once
#include <stdint.h>

// JT4
void    JT4Init(void);
void    beaconTick(void);
bool    JT4decodeCache(void);
uint8_t JT4findSync(void);
void    JT4extractBits(uint8_t bestStartIndex, uint8_t *bits);
void    JT4deInterleave(uint8_t *bits);
bool    decodeFT4(uint8_t *bits, unsigned char *dec);
void    JT4unpack(unsigned char *dec);
uint8_t toneDetect(void);
void    findMax(int tone, double *maxval, double *sn);

// PI4
void    PI4Init(void);
bool    PI4decodeCache(void);
uint8_t PI4findSync(void);
void    PI4extractBits(uint8_t bestStartIndex, uint8_t *bits);
void    PI4deInterleave(uint8_t *bits);
bool    decodePI4(uint8_t *bits, unsigned char *dec);
void    PI4unpack(unsigned char *dec);
