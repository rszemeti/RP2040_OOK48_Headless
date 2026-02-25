#pragma once
#include <stdint.h>

void    TxInit(void);
void    TxSymbol(void);
void    TxTick(void);
uint8_t encode(const char* msg, uint8_t len, uint8_t* symbols);
