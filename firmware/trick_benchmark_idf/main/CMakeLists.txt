if(NOT DEFINED TRICK_MODEL_DIR OR NOT EXISTS "${TRICK_MODEL_DIR}/trick_model_data.h")
    message(FATAL_ERROR "Pass -DTRICK_MODEL_DIR=/absolute/path/to/int8/export")
endif()
idf_component_register(SRCS "main.cc"
    INCLUDE_DIRS "../../trick_benchmark" "${TRICK_MODEL_DIR}"
    REQUIRES esp_timer esp-tflite-micro)
