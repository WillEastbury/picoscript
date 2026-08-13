cmake_minimum_required(VERSION 3.20)

add_library(picoscript_block STATIC
  pv_block.c
  pv_block_mmap.c
  pv_block_walfs.c
  pv_block_rp2350.c
  pv_block_vm.c
)
target_include_directories(picoscript_block PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/../../vm
)
if(MSVC)
  target_compile_definitions(picoscript_block PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

add_executable(test_block test_block.c)
target_link_libraries(test_block PRIVATE picoscript_block)
if(MSVC)
  target_compile_definitions(test_block PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()
