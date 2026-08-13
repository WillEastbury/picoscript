# Multi-target host provider library + smoke test.
# Linked optionally by hosted apps; freestanding PIOS builds omit platform .c.

cmake_minimum_required(VERSION 3.20)
project(picoscript_host C)

add_subdirectory(block)

add_library(picoscript_picowal_index STATIC
  picowal/portable/picowal_index.c
  picowal/storage_index_portable.c
  ../vm/picocompress.c
)
target_include_directories(picoscript_picowal_index PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/picowal
  ${CMAKE_CURRENT_SOURCE_DIR}/picowal/portable
  ${CMAKE_CURRENT_SOURCE_DIR}/../vm
)

add_library(picoscript_host STATIC
  pv_host_provider.c
  pv_host_null.c
  pv_host_pios.c
  pv_auth_store.c
  ../vm/picovm_emu.c
  ../vm/picovm_crypto_ext.c
  ../vm/pv_tensor_abi.c
  ../vm/pv_tls13_core.c
)
if(NOT MSVC)
  target_sources(picoscript_host PRIVATE ../vm/pv_ed25519_core.c)
  target_sources(picoscript_host PRIVATE ../vm/pv_curve25519_core.c)
endif()
if(WIN32)
  target_sources(picoscript_host PRIVATE pv_host_win.c)
  target_link_libraries(picoscript_host PUBLIC bcrypt advapi32 ws2_32)
else()
  target_sources(picoscript_host PRIVATE pv_host_posix.c)
  find_package(Threads REQUIRED)
  target_link_libraries(picoscript_host PUBLIC Threads::Threads)
endif()

target_include_directories(picoscript_host PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/../vm
)
target_link_libraries(picoscript_host PUBLIC picoscript_block)
target_link_libraries(picoscript_host PUBLIC picoscript_picowal_index)

# picovm + emu are compiled by the consumer; smoke tests build private copies.
add_executable(test_host_provider
  test_host_provider.c
  ../vm/picovm.c
)
target_include_directories(test_host_provider PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/../vm
)
target_link_libraries(test_host_provider PRIVATE picoscript_host)
if(MSVC)
  target_compile_definitions(test_host_provider PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

add_executable(test_emu_hooks
  test_emu_hooks.c
  ../vm/picovm.c
)
target_include_directories(test_emu_hooks PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/../vm
)
target_link_libraries(test_emu_hooks PRIVATE picoscript_host)
if(MSVC)
  target_compile_definitions(test_emu_hooks PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

add_executable(test_block_vm
  block/test_block_vm.c
  ../vm/picovm.c
)
target_include_directories(test_block_vm PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/block
  ${CMAKE_CURRENT_SOURCE_DIR}/../vm
)
target_link_libraries(test_block_vm PRIVATE picoscript_host)
if(MSVC)
  target_compile_definitions(test_block_vm PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

add_executable(test_portable_index_adapter
  picowal/test_portable_index_adapter.c
  picowal/storage_index_portable.c
  picowal/portable/picowal_index.c
  ../vm/picocompress.c
)
target_include_directories(test_portable_index_adapter PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/picowal
  ${CMAKE_CURRENT_SOURCE_DIR}/picowal/portable
  ${CMAKE_CURRENT_SOURCE_DIR}/../vm
)

add_executable(test_compute_hooks
  test_compute_hooks.c
  ../vm/picovm.c
)
target_include_directories(test_compute_hooks PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/../vm
)
target_link_libraries(test_compute_hooks PRIVATE picoscript_host)
if(MSVC)
  target_compile_definitions(test_compute_hooks PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()

add_executable(test_tls_core
  ../vm/test_tls_core.c
  ../vm/picovm.c
)
target_include_directories(test_tls_core PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/../vm
)
target_link_libraries(test_tls_core PRIVATE picoscript_host)
if(MSVC)
  target_compile_definitions(test_tls_core PRIVATE _CRT_SECURE_NO_WARNINGS)
endif()
