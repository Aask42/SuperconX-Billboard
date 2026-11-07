# PNG decode: vendored lodepng (decode-only) + badge_png wrapper -> badge_core blit.
idf_component_register(SRCS "lodepng.c" "badge_png.c"
                       INCLUDE_DIRS "include"
                       REQUIRES badge_core)

# Build only the decoder (no encoder, no C++ wrapper, no file I/O: the app reads
# the file and hands us the bytes). Shrinks lodepng's flash footprint.
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    LODEPNG_NO_COMPILE_ENCODER
    LODEPNG_NO_COMPILE_DISK
    LODEPNG_NO_COMPILE_CPP)
