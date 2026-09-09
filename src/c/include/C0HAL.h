/*
 *	Copyright (c) 2026, Signaloid.
 *
 *	Permission is hereby granted, free of charge, to any person obtaining a copy
 *	of this software and associated documentation files (the "Software"), to deal
 *	in the Software without restriction, including without limitation the rights
 *	to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 *	copies of the Software, and to permit persons to whom the Software is
 *	furnished to do so, subject to the following conditions:
 *
 *	The above copyright notice and this permission notice shall be included in all
 *	copies or substantial portions of the Software.
 *
 *	THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 *	IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 *	FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 *	AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 *	LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 *	OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 *	SOFTWARE.
 */

#pragma once

#include "SignaloidBuildTargets.h"

#include <stdint.h>
#include <stdbool.h>

#include "C0SoCStatus.h"

/*
 *	Variant register/type definitions.
 *
 *	Each compute-module variant supplies its own C0HALConfigRegister union,
 *	register-access macros and C0HALConstants enum in a dedicated header,
 *	selected here by BUILD_FOR. This keeps a variant's memory map and
 *	register layout confined to a single file.
 */
#if BUILD_FOR == SIGNALOID_C0_MICROSD
 #include "C0microSD/HAL.h"
#elif BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS
 #include "C0microSDPlus/HAL.h"
#elif BUILD_FOR == SIGNALOID_C0_SD
 #include "C0SD/HAL.h"
#else
 #error "BUILD_FOR is not set to a known Signaloid compute-module target."
#endif

/*
 *	Output (MISO) and input (MOSI) buffer accessors. These are common to
 *	all variants: they cast the variant-defined kC0HALOutputBufferOffset /
 *	kC0HALInputBufferOffset to the various element pointer types.
 */
#define kC0HALOutputBufferUint8     ((volatile uint8_t *)  kC0HALOutputBufferOffset)
#define kC0HALOutputBufferUint32    ((volatile uint32_t *) kC0HALOutputBufferOffset)
#define kC0HALOutputBufferInt32     ((volatile int32_t *)  kC0HALOutputBufferOffset)
#define kC0HALOutputBufferFloat     ((volatile float *)    kC0HALOutputBufferOffset)
#define kC0HALOutputBufferDouble    ((volatile double *)   kC0HALOutputBufferOffset)

#define kC0HALInputBufferUint8      ((volatile uint8_t *)  kC0HALInputBufferOffset)
#define kC0HALInputBufferUint32     ((volatile uint32_t *) kC0HALInputBufferOffset)
#define kC0HALInputBufferInt32      ((volatile int32_t *)  kC0HALInputBufferOffset)
#define kC0HALInputBufferFloat      ((volatile float *)    kC0HALInputBufferOffset)
#define kC0HALInputBufferDouble     ((volatile double *)   kC0HALInputBufferOffset)


/*
 *	Utility functions
 */

/**
 * Read the raw command register.
 *
 * @return Current value of the command register.
 */
uint32_t
C0HALGetCommandRegister(void);

/**
 * Read the config register.
 *
 * @return Current value of the config register as a C0HALConfigRegister.
 */
C0HALConfigRegister
C0HALGetConfigRegister(void);

/**
 * Read the status register.
 *
 * @return Current SignaloidSoCStatus held in the status register.
 */
SignaloidSoCStatus
C0HALGetStatusRegister(void);

/**
 * Write the status register.
 *
 * @param status: The status value to write.
 */
void
C0HALSetStatusRegister(SignaloidSoCStatus status);

#if BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS || BUILD_FOR == SIGNALOID_C0_SD
/**
 * Set the software-controlled onboard LED bit in the config register.
 *
 * @param state: New value for the sw_led bit.
 */
void
C0HALSetConfigRegisterSwLed(bool state);
#endif

#if BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS || BUILD_FOR == SIGNALOID_C0_SD
/**
 * Set the CPU reset bit (active low) in the config register.
 *
 * @param state: New value for the rstn bit.
 */
void
C0HALSetConfigRegisterRstn(bool state);
#endif

#if BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS || BUILD_FOR == SIGNALOID_C0_SD
/**
 * Enable software management of the onboard LED in the config register.
 *
 * @param state: New value for the sw_led_enable bit.
 */
void
C0HALSetConfigRegisterSwLedEnable(bool state);
#endif

#if BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS
/**
 * Set the red LED bit in the config register.
 *
 * @param state: New value for the red_led bit.
 */
void
C0HALSetConfigRegisterRedLed(bool state);
#endif

#if BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS || BUILD_FOR == SIGNALOID_C0_SD
/**
 * Set the green LED bit in the config register.
 *
 * @param state: New value for the green_led bit.
 */
void
C0HALSetConfigRegisterGreenLed(bool state);
#endif

#if BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS
/**
 * Set the blue LED bit in the config register.
 *
 * @param state: New value for the blue_led bit.
 */
void
C0HALSetConfigRegisterBlueLed(bool state);
#endif

#if BUILD_FOR == SIGNALOID_C0_MICROSD || BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS || BUILD_FOR == SIGNALOID_C0_SD
/**
 * Set debug pin 0 in the config register.
 *
 * @param state: New value for the debug_pin_0 bit.
 */
void
C0HALSetConfigRegisterDebugPin0(bool state);
#endif

#if BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS
/**
 * Set debug pin 1 in the config register.
 *
 * @param state: New value for the debug_pin_1 bit.
 */
void
C0HALSetConfigRegisterDebugPin1(bool state);
#endif

#if BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS
/**
 * Set debug pin 2 in the config register.
 *
 * @param state: New value for the debug_pin_2 bit.
 */
void
C0HALSetConfigRegisterDebugPin2(bool state);
#endif

#if BUILD_FOR == SIGNALOID_C0_MICROSD_PLUS || BUILD_FOR == SIGNALOID_C0_SD
/**
 * Stop the core by asserting reset (clears rstn).
 */
void
C0HALStopCore(void);
#endif

/**
 * Set the onboard LED in a variant-appropriate way.
 *
 * @param state: Desired LED state (on/off). On variants without an onboard
 *	LED this is a no-op, provided for API consistency.
 */
void
C0HALSetLed(bool state);
