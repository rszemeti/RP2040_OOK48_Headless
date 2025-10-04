
#define AppLABEL_FONT &FreeSans18pt7b    // Button label font


// Create 10 keys for the keypad
char AppLabel[3][30] = {"OOK48","JT4G Decoder","PI4 Decoder"};

// Invoke the TFT_eSPI button class and create all the button objects
TFT_eSPI_Button Appkey[3];

//------------------------------------------------------------------------------------------
int getApp(void) 
{

 int ap;
 
  tft.fillScreen(TFT_BLACK);        //Clear the TFT

  drawApp();

  bool done = false;
  while(!done)
  {
      // Pressed will be set true is there is a valid touch on the screen
      bool pressed = tft.getTouch(&t_x, &t_y);

      // / Check if any key coordinate boxes contain the touch coordinates
      for (uint8_t b = 0; b < 3; b++) 
      {
        if (pressed && Appkey[b].contains(t_x, t_y)) 
        {
          Appkey[b].press(true);  // tell the button it is pressed
        }
        else 
        {
          Appkey[b].press(false);  // tell the button it is NOT pressed
        }
      }

      // Check if any key has changed state
      for (uint8_t b = 0; b < 3; b++) 
      {

        tft.setFreeFont(AppLABEL_FONT);

        if (Appkey[b].justPressed()) 
        {
          ap=b;
          done = true;
          delay(10); // UI debouncing
        }
      }
  } 
  
  return ap;  
}


//------------------------------------------------------------------------------------------

void drawApp()
{
  char blank[2] = " ";
  // Draw the two buttons

      tft.setFreeFont(AppLABEL_FONT);

      Appkey[0].initButton(&tft, 240, 50,300,60, // x, y, w, h, outline, fill, text
                        TFT_WHITE, TFT_BLUE, TFT_WHITE,
                        blank, 1);
      Appkey[0].drawButton(0,AppLabel[0]);

      Appkey[1].initButton(&tft, 240, 150,300,60, // x, y, w, h, outline, fill, text
                        TFT_WHITE, TFT_BLUE, TFT_WHITE,
                        blank, 1);
      Appkey[1].drawButton(0,AppLabel[1]);

      Appkey[2].initButton(&tft, 240, 250,300,60, // x, y, w, h, outline, fill, text
                        TFT_WHITE, TFT_BLUE, TFT_WHITE,
                        blank, 1);
      Appkey[2].drawButton(0,AppLabel[2]);
}

//------------------------------------------------------------------------------------------

