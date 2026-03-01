#include <hardware/dma.h>
#include <hardware/adc.h>
#include "hardware/irq.h"
#include "globals.h"
#include "defines.h"
#include "dma.h"

void dma_handler(void)
{
    dma_hw->ints0 = 1u << dma_chan;
    dma_channel_set_write_addr(dma_chan, buffer[bufIndex++], true);
    if (bufIndex > 1) bufIndex = 0;
    dmaReady = true;
}

void dma_stop(void)
{
    dma_channel_set_irq0_enabled(dma_chan, false);
    dma_channel_abort(dma_chan);
    dma_hw->ints0 = 1u << dma_chan;
    dma_channel_set_irq0_enabled(dma_chan, true);
}

void dma_halt(void)
{
    adc_run(false);
    dma_channel_set_irq0_enabled(dma_chan, false);
    dma_channel_abort(dma_chan);
    dma_hw->ints0 = 1u << dma_chan;
    dma_channel_unclaim(dma_chan);
}

void dma_init(void)
{
    adc_gpio_init(26 + ADC_CHAN);
    adc_init();
    adc_select_input(ADC_CHAN);
    adc_fifo_setup(true, true, 1, false, false);
    adc_set_clkdiv(48000000 / sampleRate);
    uint dma_chan = dma_claim_unused_channel(true);
    dma_channel_config cfg = dma_channel_get_default_config(dma_chan);
    channel_config_set_transfer_data_size(&cfg, DMA_SIZE_16);
    channel_config_set_read_increment(&cfg, false);
    channel_config_set_write_increment(&cfg, true);
    channel_config_set_dreq(&cfg, DREQ_ADC);
    dma_channel_configure(dma_chan, &cfg, buffer[bufIndex], &adc_hw->fifo, dmaTransferCount, false);
    dma_channel_set_irq0_enabled(dma_chan, true);
    irq_set_exclusive_handler(DMA_IRQ_0, dma_handler);
    irq_set_enabled(DMA_IRQ_0, true);
    bufIndex = 0;
    adc_run(true);
}
