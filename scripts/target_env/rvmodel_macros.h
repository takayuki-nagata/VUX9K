#ifndef RVMODEL_MACROS_H
#define RVMODEL_MACROS_H

#define RVMODEL_HALT \
    li x1, 0x1000; \
    jalr x0, x1, 0;

#define RVMODEL_HALT_PASS RVMODEL_HALT
#define RVMODEL_HALT_FAIL RVMODEL_HALT

#define RVMODEL_DATA_BEGIN \
    .pushsection .tohost, "aw", @progbits; \
    .align 4; \
    .global begin_signature; \
begin_signature: \
    .popsection;

#define RVMODEL_DATA_END \
    .pushsection .tohost, "aw", @progbits; \
    .align 4; \
    .global end_signature; \
end_signature: \
    .popsection;

#define RVMODEL_DATA_SECTION

#define RVMODEL_IO_WRITE_STR(_R1, _R2, _R3, _STR)
#define RVMODEL_IO_INIT(_R1, _R2, _R3)

#define RVMODEL_INTERRUPT_LATENCY 0
#define RVMODEL_TIMER_INT_SOON_DELAY 0

#define RVMODEL_SET_MEXT_INT
#define RVMODEL_CLR_MEXT_INT
#define RVMODEL_SET_MSW_INT
#define RVMODEL_CLR_MSW_INT
#define RVMODEL_SET_SEXT_INT
#define RVMODEL_CLR_SEXT_INT
#define RVMODEL_SET_SSW_INT
#define RVMODEL_CLR_SSW_INT

#endif
