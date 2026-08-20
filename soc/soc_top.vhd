-- Tang Nano 9K Dual-ISA RISC-V / Hack SoC Top Level Module
-- Integrates unified_cpu, soc_ram, uart_controller, timer_core, and sdcard_spi

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity soc_top is
    Port (
        clk        : in  std_logic;
        reset      : in  std_logic;
        -- UART Pins
        uart_tx    : out std_logic;
        uart_rx    : in  std_logic;
        -- SD Card SPI Pins
        sd_sclk    : out std_logic;
        sd_mosi    : out std_logic;
        sd_miso    : in  std_logic;
        sd_cs_n    : out std_logic;
        -- Status Output Pins
        active_mode: out std_logic -- 0: Hack 16-bit, 1: RISC-V 32-bit
    );
end soc_top;

architecture Structural of soc_top is

    -- CPU Signals
    signal pc_out        : std_logic_vector(31 downto 0);
    signal instr_in      : std_logic_vector(31 downto 0);
    signal data_addr     : std_logic_vector(31 downto 0);
    signal cpu_data_out  : std_logic_vector(31 downto 0);
    signal cpu_data_in   : std_logic_vector(31 downto 0);
    signal mem_write     : std_logic;
    signal is_riscv      : std_logic;

    -- Memory Signals
    signal ram_data_out  : std_logic_vector(31 downto 0);
    signal ram_we        : std_logic;
    signal ram_we_byte   : std_logic_vector(3 downto 0);

    -- Peripheral Bus Signals
    signal uart_rdata    : std_logic_vector(7 downto 0);
    signal uart_empty    : std_logic;
    signal uart_full     : std_logic;
    signal uart_we       : std_logic;
    signal uart_re       : std_logic;

    signal timer_data_out: std_logic_vector(31 downto 0);
    signal timer_we      : std_logic;
    signal timer_re      : std_logic;
    signal timer_irq     : std_logic;

    signal sd_data_out   : std_logic_vector(31 downto 0);
    signal sd_we         : std_logic;
    signal sd_re         : std_logic;

begin

    active_mode <= is_riscv;

    -- Instantiate Unified Dual-ISA CPU Core
    cpu_inst : entity work.unified_cpu
        port map (
            clk         => clk,
            reset       => reset,
            instr_in    => instr_in,
            data_in     => cpu_data_in,
            pc_out      => pc_out,
            data_addr   => data_addr,
            data_out    => cpu_data_out,
            mem_write   => mem_write,
            active_mode => is_riscv
        );

    -- Instantiate 256KB I-RAM & 128KB D-RAM Module
    ram_inst : entity work.soc_ram
        generic map (
            I_MEM_WORDS => 65536, -- 256 KB
            D_MEM_WORDS => 32768  -- 128 KB
        )
        port map (
            clk         => clk,
            reset       => reset,
            i_addr      => pc_out,
            i_data_out  => instr_in,
            d_addr      => data_addr,
            d_data_in   => cpu_data_out,
            d_data_out  => ram_data_out,
            d_we        => ram_we,
            d_we_byte   => ram_we_byte
        );

    -- Instantiate UART Controller
    uart_inst : entity work.uart_controller
        generic map (
            CNT   => 434, -- 50MHz / 115200
            CSIZE => 10
        )
        port map (
            clk   => clk,
            rst   => reset,
            wdata => cpu_data_out(7 downto 0),
            rdata => uart_rdata,
            re    => uart_re,
            we    => uart_we,
            empty => uart_empty,
            full  => uart_full,
            txd   => uart_tx,
            rxd   => uart_rx
        );

    -- Instantiate System Timer Core
    timer_inst : entity work.timer_core
        port map (
            clk       => clk,
            reset     => reset,
            addr      => data_addr(3 downto 0),
            data_in   => cpu_data_out,
            data_out  => timer_data_out,
            we        => timer_we,
            re        => timer_re,
            timer_irq => timer_irq
        );

    -- Instantiate SD Card SPI Master Controller
    sd_inst : entity work.sdcard_spi
        port map (
            clk       => clk,
            reset     => reset,
            addr      => data_addr(3 downto 0),
            data_in   => cpu_data_out,
            data_out  => sd_data_out,
            we        => sd_we,
            re        => sd_re,
            spi_sclk  => sd_sclk,
            spi_mosi  => sd_mosi,
            spi_miso  => sd_miso,
            spi_cs_n  => sd_cs_n
        );

    -- Address Decoding & MMIO Control Logic
    process(data_addr, mem_write, ram_data_out, uart_rdata, uart_empty, uart_full, timer_data_out, sd_data_out)
    begin
        -- Default Controls
        ram_we      <= '0';
        ram_we_byte <= "1111";
        uart_we     <= '0';
        uart_re     <= '0';
        timer_we    <= '0';
        timer_re    <= '0';
        sd_we       <= '0';
        sd_re       <= '0';
        cpu_data_in <= (others => '0');

        -- Memory & Peripheral Address Decoding
        if data_addr(31 downto 28) = "0010" then
            -- 0x2000_0000 - 0x2001_FFFF: Data RAM
            ram_we      <= mem_write;
            ram_we_byte <= "1111";
            cpu_data_in <= ram_data_out;

        elsif data_addr(31 downto 28) = "0100" then
            -- 0x4000_0000 MMIO Peripheral Space
            if data_addr(15 downto 12) = "0000" then
                -- 0x4000_0000: UART Controller
                if data_addr(3 downto 0) = "0000" then
                    uart_we <= mem_write;
                    uart_re <= not mem_write;
                    cpu_data_in <= std_logic_vector(resize(unsigned(uart_rdata), 32));
                elsif data_addr(3 downto 0) = "0100" then
                    -- 0x4000_0004: UART Status Register (bit 0: empty, bit 1: full)
                    cpu_data_in <= (0 => uart_empty, 1 => uart_full, others => '0');
                end if;

            elsif data_addr(15 downto 12) = "0001" then
                -- 0x4000_1000: Timer Core
                timer_we    <= mem_write;
                timer_re    <= not mem_write;
                cpu_data_in <= timer_data_out;

            elsif data_addr(15 downto 12) = "0010" then
                -- 0x4000_2000: SD Card SPI Controller
                sd_we       <= mem_write;
                sd_re       <= not mem_write;
                cpu_data_in <= sd_data_out;
            end if;
        end if;
    end process;

end Structural;
