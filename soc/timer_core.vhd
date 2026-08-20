-- 64-bit Machine Timer Core for RISC-V (mtime / mtimecmp)
-- Compatible with Zephyr RTOS Machine Timer driver

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity timer_core is
    Port (
        clk        : in  std_logic;
        reset      : in  std_logic;
        addr       : in  std_logic_vector(3 downto 0); -- 0x0..0xF offset
        data_in    : in  std_logic_vector(31 downto 0);
        data_out   : out std_logic_vector(31 downto 0);
        we         : in  std_logic;
        re         : in  std_logic;
        timer_irq  : out std_logic
    );
end timer_core;

architecture Behavioral of timer_core is
    signal mtime    : unsigned(63 downto 0) := (others => '0');
    signal mtimecmp : unsigned(63 downto 0) := (others => '1');
begin

    -- Timer Counter Process
    process(clk, reset)
    begin
        if reset = '1' then
            mtime <= (others => '0');
            mtimecmp <= (others => '1');
        elsif rising_edge(clk) then
            -- Increment mtime every clock cycle
            mtime <= mtime + 1;

            -- MMIO Write Handling
            if we = '1' then
                case addr is
                    when "0000" => -- 0x0: mtime low
                        mtime(31 downto 0) <= unsigned(data_in);
                    when "0100" => -- 0x4: mtime high
                        mtime(63 downto 32) <= unsigned(data_in);
                    when "1000" => -- 0x8: mtimecmp low
                        mtimecmp(31 downto 0) <= unsigned(data_in);
                    when "1100" => -- 0xC: mtimecmp high
                        mtimecmp(63 downto 32) <= unsigned(data_in);
                    when others => null;
                end case;
            end if;
        end if;
    end process;

    -- MMIO Read Handling
    process(addr, mtime, mtimecmp)
    begin
        case addr is
            when "0000" => data_out <= std_logic_vector(mtime(31 downto 0));
            when "0100" => data_out <= std_logic_vector(mtime(63 downto 32));
            when "1000" => data_out <= std_logic_vector(mtimecmp(31 downto 0));
            when "1100" => data_out <= std_logic_vector(mtimecmp(63 downto 32));
            when others => data_out <= (others => '0');
        end case;
    end process;

    -- Timer Interrupt Generation: Trigger when mtime >= mtimecmp
    timer_irq <= '1' when (mtime >= mtimecmp) else '0';

end Behavioral;
