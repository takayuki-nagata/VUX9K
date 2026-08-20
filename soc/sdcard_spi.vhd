-- SD Card SPI Master Controller Core
-- Provides SPI mode access to SD Card for ROM loading & storage access

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity sdcard_spi is
    Port (
        clk        : in  std_logic;
        reset      : in  std_logic;
        addr       : in  std_logic_vector(3 downto 0); -- 0x0..0xF offset
        data_in    : in  std_logic_vector(31 downto 0);
        data_out   : out std_logic_vector(31 downto 0);
        we         : in  std_logic;
        re         : in  std_logic;
        -- SPI Physical Pin Interface
        spi_sclk   : out std_logic;
        spi_mosi   : out std_logic;
        spi_miso   : in  std_logic;
        spi_cs_n   : out std_logic
    );
end sdcard_spi;

architecture Behavioral of sdcard_spi is
    type state_type is (IDLE, TRANSFER);
    signal state      : state_type := IDLE;
    signal shift_tx   : std_logic_vector(7 downto 0) := (others => '1');
    signal shift_rx   : std_logic_vector(7 downto 0) := (others => '1');
    signal bit_count  : integer range 0 to 7 := 7;
    signal clk_div    : unsigned(7 downto 0) := to_unsigned(4, 8);
    signal clk_cnt    : unsigned(7 downto 0) := (others => '0');
    signal sclk_reg   : std_logic := '0';
    signal cs_n_reg   : std_logic := '1';
    signal busy       : std_logic := '0';
    signal rx_ready   : std_logic := '0';
begin

    spi_cs_n <= cs_n_reg;
    spi_sclk <= sclk_reg;
    spi_mosi <= shift_tx(7);

    process(clk, reset)
    begin
        if reset = '1' then
            state <= IDLE;
            sclk_reg <= '0';
            cs_n_reg <= '1';
            busy <= '0';
            rx_ready <= '0';
            shift_tx <= (others => '1');
            shift_rx <= (others => '1');
            bit_count <= 7;
            clk_cnt <= (others => '0');
            clk_div <= to_unsigned(4, 8);
        elsif rising_edge(clk) then
            -- MMIO Writes
            if we = '1' then
                case addr is
                    when "0000" => -- 0x0: Write TX Data & Start Transfer
                        if busy = '0' then
                            shift_tx <= data_in(7 downto 0);
                            busy <= '1';
                            rx_ready <= '0';
                            state <= TRANSFER;
                            bit_count <= 7;
                            clk_cnt <= (others => '0');
                            sclk_reg <= '0';
                        end if;
                    when "0100" => -- 0x4: Chip Select (CS_N)
                        cs_n_reg <= data_in(0);
                    when "1100" => -- 0xC: Clock Divisor
                        clk_div <= unsigned(data_in(7 downto 0));
                    when others => null;
                end case;
            end if;

            -- SPI State Machine
            case state is
                when IDLE =>
                    sclk_reg <= '0';

                when TRANSFER =>
                    if clk_cnt = clk_div then
                        clk_cnt <= (others => '0');
                        if sclk_reg = '0' then
                            -- Rising edge of SCLK: Sample MISO
                            sclk_reg <= '1';
                            shift_rx <= shift_rx(6 downto 0) & spi_miso;
                        else
                            -- Falling edge of SCLK: Shift MOSI
                            sclk_reg <= '0';
                            if bit_count = 0 then
                                state <= IDLE;
                                busy <= '0';
                                rx_ready <= '1';
                            else
                                shift_tx <= shift_tx(6 downto 0) & '1';
                                bit_count <= bit_count - 1;
                            end if;
                        end if;
                    else
                        clk_cnt <= clk_cnt + 1;
                    end if;
            end case;
        end if;
    end process;

    -- MMIO Read Handling
    process(addr, shift_rx, cs_n_reg, busy, rx_ready, clk_div)
    begin
        case addr is
            when "0000" => data_out <= std_logic_vector(resize(unsigned(shift_rx), 32));
            when "0100" => data_out <= (0 => cs_n_reg, others => '0');
            when "1000" => data_out <= (0 => busy, 1 => rx_ready, others => '0');
            when "1100" => data_out <= std_logic_vector(resize(clk_div, 32));
            when others => data_out <= (others => '0');
        end case;
    end process;

end Behavioral;
