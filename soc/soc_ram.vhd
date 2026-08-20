-- Dual-Port Harvard Memory Module (256 KB I-RAM + 128 KB D-RAM)
-- Supports byte-writes and preloading from hex simulation files

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;
use std.textio.all;

entity soc_ram is
    Generic (
        I_MEM_WORDS : integer := 65536; -- 256 KB (65536 x 32-bit words)
        D_MEM_WORDS : integer := 32768; -- 128 KB (32768 x 32-bit words)
        HEX_FILE    : string  := "../firmware/firmware.hex"
    );
    Port (
        clk           : in  std_logic;
        reset         : in  std_logic;
        -- Instruction Memory Interface (I-Bus)
        i_addr        : in  std_logic_vector(31 downto 0);
        i_data_out    : out std_logic_vector(31 downto 0);

        -- Data Memory Interface (D-Bus)
        d_addr        : in  std_logic_vector(31 downto 0);
        d_data_in     : in  std_logic_vector(31 downto 0);
        d_data_out    : out std_logic_vector(31 downto 0);
        d_we          : in  std_logic;
        d_we_byte     : in  std_logic_vector(3 downto 0) -- Byte write enables [3..0]
    );
end soc_ram;

architecture Behavioral of soc_ram is
    type i_mem_type is array (0 to I_MEM_WORDS - 1) of std_logic_vector(31 downto 0);
    type d_mem_type is array (0 to D_MEM_WORDS - 1) of std_logic_vector(31 downto 0);

    -- Function to load HEX file into I-RAM array
    impure function init_i_mem return i_mem_type is
        file text_file      : text;
        variable text_line  : line;
        variable temp_word  : std_logic_vector(31 downto 0);
        variable ram_content: i_mem_type := (others => (others => '0'));
        variable status     : file_open_status;
        variable i          : integer := 0;
    begin
        file_open(status, text_file, HEX_FILE, read_mode);
        if status = open_ok then
            while not endfile(text_file) and i < I_MEM_WORDS loop
                readline(text_file, text_line);
                hread(text_line, temp_word);
                ram_content(i) := temp_word;
                i := i + 1;
            end loop;
            file_close(text_file);
        else
            -- Default RISC-V Test Pattern (LUI sp, 0x20020 -> 0x200202b7)
            ram_content(0) := x"200202b7";
            ram_content(1) := x"00000013"; -- NOP
        end if;
        return ram_content;
    end function;

    signal i_mem : i_mem_type := init_i_mem;
    signal d_mem : d_mem_type := (others => (others => '0'));
begin

    -- Instruction ROM/RAM Process (I-Bus Read)
    process(clk)
        variable idx : integer;
    begin
        if rising_edge(clk) then
            idx := to_integer(unsigned(i_addr(17 downto 2)));
            if idx >= 0 and idx < I_MEM_WORDS then
                i_data_out <= i_mem(idx);
            else
                i_data_out <= (others => '0');
            end if;
        end if;
    end process;

    -- Data RAM Process (D-Bus Read & Byte-Write)
    process(clk)
        variable idx : integer;
        variable word : std_logic_vector(31 downto 0);
    begin
        if rising_edge(clk) then
            idx := to_integer(unsigned(d_addr(16 downto 2)));
            if idx >= 0 and idx < D_MEM_WORDS then
                if d_we = '1' then
                    word := d_mem(idx);
                    if d_we_byte(0) = '1' then word(7 downto 0)   := d_data_in(7 downto 0);   end if;
                    if d_we_byte(1) = '1' then word(15 downto 8)  := d_data_in(15 downto 8);  end if;
                    if d_we_byte(2) = '1' then word(23 downto 16) := d_data_in(23 downto 16); end if;
                    if d_we_byte(3) = '1' then word(31 downto 24) := d_data_in(31 downto 24); end if;
                    d_mem(idx) <= word;
                end if;
                d_data_out <= d_mem(idx);
            else
                d_data_out <= (others => '0');
            end if;
        end if;
    end process;

end Behavioral;
