#!/usr/bin/python3

import os
import subprocess
import sys

import csv
from ips_util import Patch

import text_util
import gfx_util

class StringPool:
    def __init__(self, address, capacity):
        self.address = address
        self.capacity = capacity

        self.pool = bytearray()

    def can_add(self, bytes):
        return len(self.pool) + len(bytes) < self.capacity

    def add(self, bytes):
        start = len(self.pool) + self.address

        self.pool += bytes

        return start

    def get_bytes(self):
        return self.pool

def num_8bit(num):
    return num.to_bytes(1, byteorder='little')

def num_16bit(num):
    return num.to_bytes(2, byteorder='little')

def write_with_size_check(patch, address, available_length, data, fill_byte=b'\x00'):
    difference = available_length - len(data)
    if difference < 0:
        raise Exception('Not enough space for data! Received {0} bytes, but only have space allocated for {1}.'.format(len(data), available_length))

    patch.add_record(address, data)

    if difference > 0:
        patch.add_rle_record(address + len(data), fill_byte, difference)


def write_strings_from_csv(patch, filename, reverse_font_map, pointer_table_address, pointer_table_length,
                           string_pool_address, string_pool_length, overflow_pool_address = None, overflow_pool_length = None,
                           column_to_encode=4, newline=b'\xfe', terminator=b'\xff', pad_to_line_count=1, pad_final_line=False, interleaved=False):
    pointer_table_out = bytearray()
    previously_encoded = {}

    pools = [StringPool(string_pool_address, string_pool_length)]

    if overflow_pool_address is not None and overflow_pool_length is not None:
        pools.append(StringPool(overflow_pool_address, overflow_pool_length))

    with open(filename, 'r', encoding='shift-jis') as in_file:
        reader = csv.reader(in_file, lineterminator='\n')
        for i, row in enumerate(reader):
            if interleaved:
                # This is only used for area names, which have some special flags that need to be set, except for index 15.
                flag_map = {7: 0x2, 9: 0x4, 10: 0x8, 16: 0x8}
                encoded_string = text_util.encode_text_interleaved(row[4], reverse_font_map, i != 15, flag_map[i] if i in flag_map else 0x1)
            else:
                encoded_string = text_util.encode_text(row[column_to_encode], reverse_font_map,
                                                       pad_to_line_count=pad_to_line_count, pad_final_line=pad_final_line,
                                                       newline=newline, terminator=terminator)

            string_address = None
            if encoded_string in previously_encoded:
                string_address = previously_encoded[encoded_string]
            else:
                for pool in pools:
                    if pool.can_add(encoded_string):
                        string_address = (0xffff & pool.add(encoded_string))
                        break

                if string_address is not None:
                    previously_encoded[encoded_string] = string_address

            if string_address is None:
                print('Text {0} didn\'t fit!'.format(row[4]))
                pointer_table_out += (0xffff).to_bytes(2, byteorder='little')
            else:
                pointer_table_out += string_address.to_bytes(2, byteorder='little')

    write_with_size_check(patch, pointer_table_address, pointer_table_length, pointer_table_out)
    for pool in pools:
        write_with_size_check(patch, pool.address, pool.capacity, pool.get_bytes(), fill_byte=b'\xff')

def write_gfx(patch, data, address, length):
    write_with_size_check(patch, address, length, gfx_util.compress(data))

def write_gfx_from_file(patch, filename, address, length):
    with open(filename, 'rb') as f:
        write_gfx(patch, f.read(), address, length)

def write_code(patch, filename, address, length):
    tmp_filename = 'build/_tmp.a65'
    result = subprocess.run(['xa', '-o', tmp_filename, '-w', filename], stderr=subprocess.PIPE)
    if result.returncode == 0:
        with open(tmp_filename, 'rb') as tmp_file:
            write_with_size_check(patch, address, length, tmp_file.read(), fill_byte=b'\xea')
        os.remove(tmp_filename)
    else:
        raise Exception('Assembler failed on {0} with error code {1}:\n\nErrors:\n{2}'.format(filename, result.returncode, result.stderr.decode(sys.stderr.encoding)))


if __name__ == '__main__':
    os.makedirs('build', exist_ok=True)

    reverse_font_map = text_util.load_map_reverse('assets/text/font.tbl')

    patch = Patch()

    # New tiles for digits in font.
    patch.add_record(0x488a, b'\xB5\xB6\xB7\xB8')

    # Evolution options...
    # These instructions write blank to each possible location of the arrow.
    # Nudge each one up by 0x40...
    patch.add_record(0x626b, b'\x06')
    patch.add_record(0x626f, b'\x86')
    patch.add_record(0x6273, b'\x06')
    patch.add_record(0x6277, b'\x86')
    patch.add_record(0x627B, b'\x06')

    # Do the same with a table of pointers used for writing the actual arrow.
    patch.add_record(0x6325, b'\x06')
    patch.add_record(0x6327, b'\x86')
    patch.add_record(0x6329, b'\x06')
    patch.add_record(0x632b, b'\x86')
    patch.add_record(0x632d, b'\x06')

    write_code(patch, 'assets/code/menu text.asm', 0x4f90, 309)

    # This assembly code sets the height of the area name window. Make it shorter.
    patch.add_record(0x1c2af, num_16bit(4))

    # Assembly code to render text for save slots. The text used comes from the area names, I think?

    # NOP out some instructions that skip the first few bytes of the text.
    patch.add_rle_record(0x1c441, b'\xea', 4)

    # I rewrote one whole block for simplicity... I think the changes are still basically just constants where
    # the chapter digits come from. I'm honestly not sure what exactly this does differently, though; the file
    # I originally compiled it from seems to have disappeared.
    write_code(patch, 'assets/code/save slot text.asm', 0x1c46f, 52)

    # These are the destination offsets for the number of areas cleared and total areas in the chapter, respectively.
    # I'm just shifting them left by one character (2 bytes) to accomodate the translated string.
    patch.add_record(0x1c4b4, num_16bit(0x004a))
    patch.add_record(0x1c4de, num_16bit(0x0051))

    write_strings_from_csv(patch, 'assets/text/area_names.csv', reverse_font_map, 0x1c9db, 108 * 2, 0x1cab3, 2048, interleaved=True)

    write_strings_from_csv(patch, 'assets/text/dialog_bank_1.csv', reverse_font_map, 0x1d2b3, 29 * 2, 0x1d2ed, 6766, pad_to_line_count=6, pad_final_line=True)
    write_strings_from_csv(patch, 'assets/text/dialog_bank_2.csv', reverse_font_map, 0xfb719, 81 * 2, 0xfb7bb, 18185, 0xfa730, 928, pad_to_line_count=6, pad_final_line=True)
    write_strings_from_csv(patch, 'assets/text/dialog_bank_3.csv', reverse_font_map, 0xedfc1, 33 * 2, 0xee011, 6684, pad_to_line_count=6, pad_final_line=True)

    # Before the pointer table for each of these menus, there's a block of 8 bytes per entry describing the size of the window.
    # Starting address, width, height. The fourth word is a flag of some kind, but I'm not sure what it does.

    # Area menu
    patch.add_record(0xf8122, num_16bit(17) + num_16bit(13)) # Window for the main window is a bit bigger.
    patch.add_record(0xf816c, num_16bit(6))                  # "You cannot restore a creature from the future" is a little shorter.
    write_strings_from_csv(patch, 'assets/text/menu_area.csv', reverse_font_map, 0xf8170, 19 * 2, 0xf8196, 1378, newline=b'\xff\xfe', terminator=b'\xff\xff')

    # Evolution menu
    patch.add_record(0xf8708, num_16bit(0x01ef) + num_16bit(15)) # "Are you sure?" stretches to the left.
    patch.add_record(0xf8710, num_16bit(0x01ef) + num_16bit(15)) # "Not enough EP!" does the same.
    patch.add_record(0xf871c, num_16bit(9))                      # "Time flows by rapidly" gets shorter...
    patch.add_record(0xf872c, num_16bit(9))                      #   same for "An unfamiliar environment"
    patch.add_record(0xf8734, num_16bit(9))                      #   and "Crystal's power is depleted"
    patch.add_record(0xf873c, num_16bit(9))                      #   and "Crystal's power accelerates your evolution"
    patch.add_record(0xf8744, num_16bit(9))                      #   and "evolve into a bird.
    write_strings_from_csv(patch, 'assets/text/menu_evo.csv', reverse_font_map, 0xf8748, 10 * 2, 0xf875c, 1008, newline=b'\xff\xfe', terminator=b'\xff\xff')

    # Map menu
    patch.add_record(0xf8b4e, num_16bit(17) + num_16bit(13))    # Window for the main menu is a bit bigger.
    patch.add_record(0xf8b5e, num_16bit(15))                    # "Are you sure?" (for saving) gets wider.
    patch.add_record(0xf8b68, num_16bit(4))                     # "Save data recorded" gets shorter.
    patch.add_record(0xf8b94, num_16bit(0x01c7))                # "There are no records" moves down a row
    patch.add_record(0xf8b98, num_16bit(4))                     #   and get shorter to compensate.
    patch.add_record(0xf8bae, num_16bit(15))                    # "Are you sure?" (for deleting) gets wider.
    patch.add_record(0xf8bb4, num_16bit(0x010b) + num_16bit(4)) # "Save data deleted" moves down and gets shorter.
    patch.add_record(0xf8bce, num_16bit(15))                    # "Are you sure?" (for deleting record entries) gets shorter.
    patch.add_record(0xf8bd4, num_16bit(0x0127))                # "Entry deleted" moves down...
    patch.add_record(0xf8bd8, num_16bit(4))                     #   and gets shorter.
    write_strings_from_csv(patch, 'assets/text/menu_map.csv', reverse_font_map, 0xf8bdc, 18 * 2, 0xf8c00, 1366, newline=b'\xff\xfe', terminator=b'\xff\xff')

    # Prologue and title screen strings... no window borders associated with these.
    write_strings_from_csv(patch, 'assets/text/menu_prologue.csv', reverse_font_map, 0xf9156, 5 * 2, 0xf9160, 288, newline=b'\xff\xfe', terminator=b'\xff\xff')
    write_strings_from_csv(patch, 'assets/text/menu_title.csv', reverse_font_map, 0xf9280, 3 * 2, 0xf9286, 214, newline=b'\xff\xfe', terminator=b'\xff\xff')

    # Load menu
    patch.add_record(0xf9366, num_16bit(15)) # "Are you sure?" gets wider.
    patch.add_record(0xf9370, num_16bit(4))  # "Save data loaded" gets shorter.
    write_strings_from_csv(patch, 'assets/text/menu_load.csv', reverse_font_map, 0xf9374, 3 * 2, 0xf937a, 206, newline=b'\xff\xfe', terminator=b'\xff\xff')

    # "Inserted text" is only used for storing classification text like "Fish", "Amphibian", etc. that gets inserted into the status displays.
    # The original ROM has some other unused text there, but we just blank it all out to save room.
    # Note that the space saved here gets used for overflow in the 0xfb719 dialog block.
    # If for some reason the inserted text ever gets any bigger, make sure to update the overflow block's start address and size too.
    write_strings_from_csv(patch, 'assets/text/menu_inserted_text.csv', reverse_font_map, 0xfa660, 55 * 2, 0xfa6e0, 96, newline=b'\xff\xfe', terminator=b'\xff\xff')



    write_strings_from_csv(patch, 'assets/text/evo_options.csv', reverse_font_map, 0xfaae0, 28 * 2, 0xfab20, 3065)

    # Credits... odd format here, and I'm not entirely sure how it works.
    with open('assets/text/credits.txt', 'r') as f:
        write_with_size_check(patch, 0x13f516, 1949, text_util.encode_text(f.read(), reverse_font_map, newline=b'\x0d', terminator=b''))

    # The health and EP displays... 16-bit tiles.
    patch.add_record(0xf8060, b'\x87\x30\x8f\x30\xdc')                             # "HP:"
    patch.add_record(0xf8078, b'\x84\x30\x8f\x30\xdc\x30\x00\x30\x00\x30\x00\x30') # "EP:   " (Note three spaces)

    # "Pause" text... just constants embedded in assembly.
    patch.add_record(0x1c67d, text_util.map_char('P', reverse_font_map))
    patch.add_record(0x1c684, text_util.map_char('a', reverse_font_map))
    patch.add_record(0x1c68b, text_util.map_char('u', reverse_font_map))
    patch.add_record(0x1c692, text_util.map_char('s', reverse_font_map))
    patch.add_record(0x1c699, text_util.map_char('e', reverse_font_map))

    # And HDMA tables...
    patch.add_record(0x1160f, b'\x7b') # Standalone yes/no confirmation on evo menu; make slightly wider on the left.
    patch.add_record(0x11618, b'\x42') # Some window a little shorter; might be one of the evolution messages.
    patch.add_record(0x11622, b'\x42') # I think this is the red crystal message. Make it a bit shorter.

    patch.add_record(0x11669, b'\x1e') # "Save data recorded." Wider on the left.
    patch.add_record(0x11679, b'\x8d') # "Where will you record your save data?" - Menu window wider on right
    patch.add_record(0x1167a, b'\x46') #                                         - and taller on bottom.
    patch.add_record(0x1167d, b'\x10') #                                         - Shorten the save window to compensate.
    patch.add_record(0x11689, b'\x8d') # "Are you sure?" (for saving) - Menu window wider on right
    patch.add_record(0x1168a, b'\x46') #                                and taller on bottom.
    patch.add_record(0x1168d, b'\x10') #                                Shorten the save window to compensate.
    patch.add_record(0x11692, b'\xb5') #                                Wider confirmation window.
    patch.add_record(0x116ad, b'\x60') # "There are no records!" - Menu window wider on right
    patch.add_record(0x116ae, b'\x0b') #                           and taller on bottom.
    patch.add_record(0x116b0, b'\x1a') #                           Compensate with shorter message window.
    patch.add_record(0x116bc, b'\x8d') # "This will overwrite..." (for saving) - Menu window wider on right
    patch.add_record(0x116bd, b'\x46') #                                         and taller on bottom.
    patch.add_record(0x116c0, b'\x10') #                                         Shorten the save window to compensate.
    patch.add_record(0x116d3, b'\x1c') # Believe this to be the area name window.
    patch.add_record(0x116ff, b'\x62') # Used by both the area and map root menus. - Window becomes taller
    patch.add_record(0x11701, b'\x8d') #                                             and wider.
    patch.add_record(0x117e0, b'\x8d') # "Which entry would you like to view?" - Menu window wider on right
    patch.add_record(0x117e1, b'\x2c') #                                         and taller on bottom.
    patch.add_record(0x117e4, b'\x2e') #                                         Shorten next region to compensate.
    patch.add_record(0x1180e, b'\x8d') # "Which save data will you delete?" - Menu window wider on right
    patch.add_record(0x1180f, b'\x3e') #                                      and taller on bottom.
    patch.add_record(0x11812, b'\x18') #                                      Shorten next region to compensate.
    patch.add_record(0x1181e, b'\x8d') # "Are you sure?" (for deleting) - Menu window wider on right
    patch.add_record(0x1181f, b'\x3e') #                                  and taller on bottom.
    patch.add_record(0x11822, b'\x18') #                                  Shorten next region to compensate.
    patch.add_record(0x11827, b'\xb5') #                                  Wider confirmation window.
    patch.add_record(0x1182c, b'\x49') # "Save data deleted" - Window starts earlier on top
    patch.add_record(0x1182f, b'\x16') #                       and isn't as tall.
    patch.add_record(0x1183b, b'\x8d') # "Which entry will you delete?" - Menu window wider on right
    patch.add_record(0x1183c, b'\x1c') #                                  and taller on bottom.
    patch.add_record(0x1183f, b'\x3e') #                                  Shorten next region to compensate.
    patch.add_record(0x1184b, b'\x8d') # "Are you sure?" (for deleting records) - Menu window wider on right
    patch.add_record(0x1184c, b'\x1c') #                                          and taller on bottom.
    patch.add_record(0x1184f, b'\x14') #                                          Shorten next region to compensate.
    patch.add_record(0x11854, b'\xc5') #                                          Wider confirmation window
    patch.add_record(0x11857, b'\xcd') #                                          continuing to next region.

    # Dialog window dimensions... mostly constants in assembly.
    # All I'm doing is changing the width of text to 22 characters to fill the existing window and shifting the text over by
    # one tile to compensate. Bunch of redundant constants need to get touched for that.
    dialog_width, dialog_height,  = 22, 6
    dialog_start_addr_prologue, dialog_start_addr_area = 0xe08a, 0xe1ca

    patch.add_record(0x1b63f, (dialog_width * 2).to_bytes(2, byteorder='little'))                 # Offset in bytes between lines in the dialog buffer,
    patch.add_record(0x1b695, (dialog_width * 2).to_bytes(2, byteorder='little'))                 #   and again for scrolling.
    patch.add_record(0x1b6c1, (dialog_width * dialog_height * 2).to_bytes(2, byteorder='little')) # Length of a page in bytes.
    patch.add_record(0x1b6c9, dialog_width.to_bytes(2, byteorder='little'))                       # Width of dialog window in characters.
    patch.add_record(0x1b6d4, (64 - dialog_width * 2).to_bytes(2, byteorder='little'))            # Offset in bytes from end of line in the tilemap to start of the next.
    patch.add_record(0x1efca, dialog_start_addr_area.to_bytes(2, byteorder='little'))             # Start address of dialog text on initial page,
    patch.add_record(0x1f000, dialog_start_addr_area.to_bytes(2, byteorder='little'))             #   and on subsequent pages.
    patch.add_record(0x1f2df, dialog_start_addr_prologue.to_bytes(2, byteorder='little'))         #   Same thing for the prologue text.
    patch.add_record(0x1f31f, dialog_start_addr_prologue.to_bytes(2, byteorder='little'))         #

    # Change the palette for the prologue dialog windows to gray/dark gray instead of white/red.
    prolog_dialog_palette = 0x2000
    patch.add_record(0x1f2d1, prolog_dialog_palette.to_bytes(2, byteorder='little')) # Used for text.
    patch.add_record(0x1f36d, prolog_dialog_palette.to_bytes(2, byteorder='little')) # Used for the arrow.

    # Instructions in the area of 0x1f1ae are responsible for loading the prologue text. Update some constants there.
    patch.add_record(0x1f1af, b'\x02') # Start music at index 2.
    patch.add_record(0x1f1dc, b'\x05') # End the prologue and move on to the sun scene at index 5.

    # Tilemap for the chapter graphics and possibly some other things.
    write_gfx_from_file(patch, 'assets/gfx/chapter_tilemap.bin', 0x4efec, 1488)

    # There's a section of the font tiles that gets replaced with the evolution buttons while that menu is
    # open, and then reloaded from a different compressed image. Write both of those from the source asset.
    with open('assets/gfx/font.bin', 'rb') as f:
        font_data = f.read()
        write_gfx(patch, font_data, 0x79358, 2578)
        write_gfx(patch, font_data[0x200:0x600], 0x77c7e, 711)

    # Evolution menu buttons
    write_gfx_from_file(patch, 'assets/gfx/evo_buttons.bin', 0x7efe0, 860)

    # Title image
    write_gfx_from_file(patch, 'assets/gfx/title.bin', 0x11acb2, 3990)

    # Chapter title graphics
    write_gfx_from_file(patch, 'assets/gfx/chapter.bin', 0x110c06, 2022)

    # "Triconodon" image from chapter 4 intro
    write_gfx_from_file(patch, 'assets/gfx/triconodon.bin', 0x1247e2, 2248)

    # All done! Build the patch now...
    with open('build/test.ips', 'w+b') as f:
        f.write(patch.encode())

    # Apply the patch to a ROM file, if one was specified at the command line.
    if len(sys.argv) > 1:
        rom_data = bytes()
        with open(sys.argv[1], 'rb') as f:
            rom_data = f.read()

        rom_data = patch.apply(rom_data)

        with open('build/test.sfc', 'wb') as f:
            f.write(rom_data)
