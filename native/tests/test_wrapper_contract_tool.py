# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "native" / "tools"))

import wrapper_contract_tool  # noqa: E402


CONTRACT = REPOSITORY_ROOT / "native" / "wrapper" / "jni-contract.toml"
CPP = REPOSITORY_ROOT / "native" / "wrapper" / "zivplayer_mpv.cpp"
CMAKE = REPOSITORY_ROOT / "native" / "wrapper" / "CMakeLists.txt"
KOTLIN = (
    REPOSITORY_ROOT
    / "platform"
    / "libmpv-android"
    / "src"
    / "main"
    / "kotlin"
    / "io"
    / "github"
    / "joyelliot"
    / "zivplayer"
    / "platform"
    / "libmpv"
    / "MpvNativeBindings.kt"
)


class WrapperContractToolTest(unittest.TestCase):
    def test_committed_wrapper_contract_matches_both_languages(self) -> None:
        self.assertEqual(16, wrapper_contract_tool.validate(CONTRACT, CPP, KOTLIN, CMAKE))

    def test_registration_descriptor_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "wrapper.cpp"
            changed.write_text(
                CPP.read_text(encoding="utf-8").replace(
                    '"(JLjava/lang/String;D)I"',
                    '"(JLjava/lang/String;J)I"',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, changed, KOTLIN, CMAKE)

    def test_registration_function_pointer_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "wrapper.cpp"
            original = CPP.read_text(encoding="utf-8")
            mutation = original.replace(
                "reinterpret_cast<void*>(GuardedNativeSetPropertyDouble)}",
                "reinterpret_cast<void*>(GuardedNativeSetPropertyBoolean)}",
                1,
            )
            self.assertNotEqual(original, mutation)
            changed.write_text(mutation, encoding="utf-8")
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, changed, KOTLIN, CMAKE)

    def test_commented_registration_entry_is_not_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "wrapper.cpp"
            original = CPP.read_text(encoding="utf-8")
            entry = (
                '        {const_cast<char*>("nativeCreate"),\n'
                '         const_cast<char*>("(Landroid/content/Context;)J"),\n'
                '         reinterpret_cast<void*>(GuardedNativeCreate)},'
            )
            mutation = original.replace(entry, f"        /* {entry.strip()} */", 1)
            self.assertNotEqual(original, mutation)
            changed.write_text(mutation, encoding="utf-8")
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, changed, KOTLIN, CMAKE)

    def test_register_natives_count_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "wrapper.cpp"
            changed.write_text(
                CPP.read_text(encoding="utf-8").replace(
                    "sizeof(kMethods[0])",
                    "sizeof(kMethods[1])",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, changed, KOTLIN, CMAKE)

    def test_wire_index_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "bindings.kt"
            changed.write_text(
                KOTLIN.read_text(encoding="utf-8").replace(
                    "internal const val INTEGER_END_ERROR = 4",
                    "internal const val INTEGER_END_ERROR = 6",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, CPP, changed, CMAKE)

    def test_duplicate_wire_value_is_rejected_before_source_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "contract.toml"
            changed.write_text(
                CONTRACT.read_text(encoding="utf-8").replace(
                    'cpp = "kPropertyFlag"\nvalue = 7',
                    'cpp = "kPropertyFlag"\nvalue = 6',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(changed, CPP, KOTLIN, CMAKE)

    def test_kotlin_package_object_and_static_drift_are_rejected(self) -> None:
        original = KOTLIN.read_text(encoding="utf-8")
        mutations = (
            original.replace(
                "package io.github.joyelliot.zivplayer.platform.libmpv",
                "package io.github.joyelliot.zivplayer.platform.renamed",
                1,
            ),
            original.replace("internal object MpvNativeBindings", "internal object RenamedBindings", 1),
            original.replace("    private external fun nativeCreate", "    @JvmStatic\n    private external fun nativeCreate", 1),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                changed = Path(temporary) / "bindings.kt"
                changed.write_text(mutation, encoding="utf-8")
                with self.assertRaises(wrapper_contract_tool.ContractError):
                    wrapper_contract_tool.validate(CONTRACT, CPP, changed, CMAKE)

    def test_comment_and_string_decoys_cannot_hide_constant_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            changed_cpp = Path(temporary) / "wrapper.cpp"
            changed_cpp.write_text(
                CPP.read_text(encoding="utf-8").replace(
                    "    kEventId = 0,",
                    "    // kEventId = 0,\n    kEventId = 1,",
                    1,
                ),
                encoding="utf-8",
            )
            changed_kotlin = Path(temporary) / "bindings.kt"
            changed_kotlin.write_text(
                KOTLIN.read_text(encoding="utf-8").replace(
                    "internal object MpvNativeBindings : MpvNativeApi {",
                    'internal object MpvNativeBindings : MpvNativeApi {\n'
                    '    private val decoy = """private const val LIBRARY_NAME = '
                    '"zivplayer_mpv""""',
                    1,
                ).replace(
                    'private const val LIBRARY_NAME = "zivplayer_mpv"',
                    'private const val LIBRARY_NAME = "renamed"',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, changed_cpp, KOTLIN, CMAKE)
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, CPP, changed_kotlin, CMAKE)

    def test_cmake_output_name_prefix_and_suffix_drift_are_rejected(self) -> None:
        original = CMAKE.read_text(encoding="utf-8")
        mutations = (
            original.replace(
                "OUTPUT_NAME zivplayer_mpv",
                "OUTPUT_NAME renamed_mpv",
                1,
            ),
            original.replace("        PREFIX lib", "        PREFIX renamed_", 1),
            original.replace("        SUFFIX .so", "        SUFFIX .bad", 1),
            original.replace('        RELEASE_POSTFIX ""', "        RELEASE_POSTFIX _d", 1),
            original.replace(
                'if(NOT CMAKE_BUILD_TYPE STREQUAL "Release")',
                'if(NOT CMAKE_BUILD_TYPE STREQUAL "Debug")',
                1,
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for index, mutation in enumerate(mutations):
                self.assertNotEqual(original, mutation)
                with self.subTest(index=index):
                    changed = Path(temporary) / f"CMakeLists-{index}.txt"
                    changed.write_text(mutation, encoding="utf-8")
                    with self.assertRaises(wrapper_contract_tool.ContractError):
                        wrapper_contract_tool.validate(CONTRACT, CPP, KOTLIN, changed)

    def test_guard_alias_signature_and_noexcept_drift_are_rejected(self) -> None:
        original = CPP.read_text(encoding="utf-8")
        mutations = (
            "#define GuardedNativeCreate GuardedNativeInitialize\n" + original,
            "#if 1\n" + original + "\n#endif\n",
            "??=define GuardedNativeCreate GuardedNativeInitialize\n" + original,
            "%:define GuardedNativeCreate GuardedNativeInitialize\n" + original,
            '#include "evil.h"\n' + original,
            original.replace(
                "jlong GuardedNativeCreate(\n",
                "jint GuardedNativeCreate(\n",
                1,
            ),
            original.replace(
                "        jobject application_context) noexcept {",
                "        jobject application_context) {",
                1,
            ),
        )
        for index, mutation in enumerate(mutations):
            self.assertNotEqual(original, mutation)
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                changed = Path(temporary) / "wrapper.cpp"
                changed.write_text(mutation, encoding="utf-8")
                with self.assertRaises(wrapper_contract_tool.ContractError):
                    wrapper_contract_tool.validate(CONTRACT, changed, KOTLIN, CMAKE)

    def test_guard_helper_and_registration_failure_check_drift_are_rejected(self) -> None:
        original = CPP.read_text(encoding="utf-8")
        shadowed_guard = original.replace(
            "#include <exception>",
            "#include <exception>\n#include <functional>",
            1,
        ).replace(
            "jint GuardedNativeInitialize(JNIEnv* env, jobject receiver, jlong token) noexcept {\n"
            "    return GuardStatus",
            "jint GuardedNativeInitialize(JNIEnv* env, jobject receiver, jlong token) noexcept {\n"
            "    auto GuardStatus = [](JNIEnv*, const std::function<jint()>& function) noexcept {\n"
            "        return function();\n"
            "    };\n"
            "    return GuardStatus",
            1,
        )
        mutations = (
            original.replace(
                "    } catch (const std::exception&) {\n"
                "        ThrowUnexpectedNativeFailure(env);\n"
                "        return MPV_ERROR_GENERIC;",
                "    } catch (...) {\n"
                "        ThrowUnexpectedNativeFailure(env);\n"
                "        return MPV_ERROR_GENERIC;",
                1,
            ),
            original.replace("const jint status = env->RegisterNatives", "env->RegisterNatives", 1),
            original.replace("status != JNI_OK || env->ExceptionCheck()", "false", 1),
            shadowed_guard,
        )
        for index, mutation in enumerate(mutations):
            self.assertNotEqual(original, mutation)
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                changed = Path(temporary) / "wrapper.cpp"
                changed.write_text(mutation, encoding="utf-8")
                with self.assertRaises(wrapper_contract_tool.ContractError):
                    wrapper_contract_tool.validate(CONTRACT, changed, KOTLIN, CMAKE)

    def test_jni_onload_scope_class_and_count_decoys_are_rejected(self) -> None:
        original = CPP.read_text(encoding="utf-8")
        wrong_register = original.replace(
            "            bindings,\n            kMethods,",
            "            otherBindings,\n            kMethods,",
            1,
        ).replace(
            'extern "C" JNIEXPORT jint JNICALL JNI_OnLoad',
            "jint DecoyRegister(JNIEnv* env, jclass bindings) {\n"
            "    return env->RegisterNatives(\n"
            "            bindings, kMethods,\n"
            "            static_cast<jint>(sizeof(kMethods) / sizeof(kMethods[0])));\n"
            "}\n\n"
            'extern "C" JNIEXPORT jint JNICALL JNI_OnLoad',
            1,
        )
        wrong_find = original.replace(
            "env->FindClass(kBindingsClass)",
            'env->FindClass("wrong/Class")',
            1,
        ).replace(
            'extern "C" JNIEXPORT jint JNICALL JNI_OnLoad',
            "jclass DecoyFind(JNIEnv* env) { return env->FindClass(kBindingsClass); }\n\n"
            'extern "C" JNIEXPORT jint JNICALL JNI_OnLoad',
            1,
        )
        shadowed_class = original.replace(
            "    jclass bindings = env->FindClass(kBindingsClass);",
            '    const char* kBindingsClass = "wrong/Class";\n'
            "    jclass bindings = env->FindClass(kBindingsClass);",
            1,
        )
        shadowed_methods = original.replace(
            "    const jint status = env->RegisterNatives(",
            "    const JNINativeMethod* kMethods = nullptr;\n"
            "    const jint status = env->RegisterNatives(",
            1,
        )
        fake_environment = original.replace(
            "    jclass bindings = env->FindClass(kBindingsClass);",
            "    JNIEnv* fake_env = nullptr;\n"
            "    jclass bindings = fake_env->FindClass(kBindingsClass);",
            1,
        ).replace("env->RegisterNatives", "fake_env->RegisterNatives", 1).replace(
            "env->ExceptionCheck()",
            "fake_env->ExceptionCheck()",
            1,
        )
        reassigned_environment = original.replace(
            "    jclass bindings = env->FindClass(kBindingsClass);",
            "    env = nullptr;\n"
            "    jclass bindings = env->FindClass(kBindingsClass);",
            1,
        )
        reassigned_bindings = original.replace(
            "    const jint status = env->RegisterNatives(",
            "    bindings = nullptr;\n"
            "    const jint status = env->RegisterNatives(",
            1,
        )
        reassigned_vm = original.replace(
            "    if (av_jni_set_java_vm(vm, nullptr) < 0) {",
            "    vm = nullptr;\n"
            "    if (av_jni_set_java_vm(vm, nullptr) < 0) {",
            1,
        )
        mutations = (
            original.replace("JNI_OnLoad(JavaVM*", "NotJNI_OnLoad(JavaVM*", 1),
            wrong_register,
            wrong_find,
            shadowed_class,
            shadowed_methods,
            fake_environment,
            reassigned_environment,
            reassigned_bindings,
            reassigned_vm,
        )
        for index, mutation in enumerate(mutations):
            self.assertNotEqual(original, mutation)
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                changed = Path(temporary) / "wrapper.cpp"
                changed.write_text(mutation, encoding="utf-8")
                with self.assertRaises(wrapper_contract_tool.ContractError):
                    wrapper_contract_tool.validate(CONTRACT, changed, KOTLIN, CMAKE)

    def test_kotlin_nested_extra_and_rewritten_native_members_are_rejected(self) -> None:
        original = KOTLIN.read_text(encoding="utf-8")
        nested_object = original.replace(
            "/** JNI transport for the source-built libzivplayer_mpv wrapper. */\n",
            "/** JNI transport for the source-built libzivplayer_mpv wrapper. */\n"
            "private class Outer {\n",
            1,
        ) + "\n}"
        nested_method = original.replace(
            "    private external fun nativeCreate(applicationContext: Context): Long",
            "    private class Nested {\n"
            "        private external fun nativeCreate(applicationContext: Context): Long\n"
            "    }",
            1,
        )
        extra_method = original.replace(
            "    private external fun nativeCreate(applicationContext: Context): Long",
            "    internal external fun nativeUndeclared(): Int\n\n"
            "    private external fun nativeCreate(applicationContext: Context): Long",
            1,
        )
        rewritten = original.replace(
            "    private external fun nativeCreate(applicationContext: Context): Long",
            "    @kotlin.jvm.JvmStatic\n"
            "    private external fun nativeCreate(applicationContext: Context): Long",
            1,
        )
        aliased_annotation = original.replace(
            "import android.view.Surface",
            "import android.view.Surface\nimport kotlin.jvm.JvmStatic as NativeStatic",
            1,
        ).replace(
            "    private external fun nativeCreate(applicationContext: Context): Long",
            "    @NativeStatic\n"
            "    private external fun nativeCreate(applicationContext: Context): Long",
            1,
        )
        shadowed_library_name = original.replace(
            "        System.loadLibrary(LIBRARY_NAME)",
            '        val LIBRARY_NAME = "evil"\n'
            "        System.loadLibrary(LIBRARY_NAME)",
            1,
        )
        shadowed_system = original.replace(
            "        System.loadLibrary(LIBRARY_NAME)",
            "        val System = object { fun loadLibrary(name: String) = Unit }\n"
            "        System.loadLibrary(LIBRARY_NAME)",
            1,
        )
        shadowed_native = original.replace(
            "        return nativeCreate(applicationContext)",
            "        fun nativeCreate(context: Context) = 0L\n"
            "        return nativeCreate(applicationContext)",
            1,
        )
        public_wire_constant = original.replace(
            "    internal const val INTEGER_EVENT_ID = 0",
            "    const val INTEGER_EVENT_ID = 0",
            1,
        )
        private_wire_constant = original.replace(
            "    internal const val INTEGER_EVENT_ID = 0",
            "    private const val INTEGER_EVENT_ID = 0",
            1,
        )
        for index, mutation in enumerate(
            (
                nested_object,
                nested_method,
                extra_method,
                rewritten,
                aliased_annotation,
                shadowed_library_name,
                shadowed_system,
                shadowed_native,
                public_wire_constant,
                private_wire_constant,
            ),
        ):
            self.assertNotEqual(original, mutation)
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                changed = Path(temporary) / "bindings.kt"
                changed.write_text(mutation, encoding="utf-8")
                with self.assertRaises(wrapper_contract_tool.ContractError):
                    wrapper_contract_tool.validate(CONTRACT, CPP, changed, CMAKE)

    def test_wire_contract_cannot_omit_constants_or_accept_scoped_decoys(self) -> None:
        original_contract = CONTRACT.read_text(encoding="utf-8")
        removed_contract_entry = original_contract.replace(
            '\n[[wire.presenceFlags]]\nkotlin = "PRESENCE_PROPERTY_VALUE"\n'
            'cpp = "kHasPropertyValue"\nvalue = 16\n',
            "\n",
            1,
        )
        self.assertNotEqual(original_contract, removed_contract_entry)
        original_cpp = CPP.read_text(encoding="utf-8")
        cpp_decoy = original_cpp.replace(
            "    kEventId = 0,",
            "    kEventId = (3),",
            1,
        ).replace(
            "struct Client {",
            "void DecoyWireConstant() { int kEventId = 0; }\n\nstruct Client {",
            1,
        )
        original_kotlin = KOTLIN.read_text(encoding="utf-8")
        kotlin_decoy = original_kotlin.replace(
            "    internal const val INTEGER_EVENT_ID = 0",
            "    internal const val INTEGER_EVENT_ID = (3)",
            1,
        ).replace(
            "internal object MpvNativeBindings : MpvNativeApi {",
            "internal const val INTEGER_EVENT_ID = 0\n\n"
            "internal object MpvNativeBindings : MpvNativeApi {",
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            changed_contract = Path(temporary) / "contract.toml"
            changed_contract.write_text(removed_contract_entry, encoding="utf-8")
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(changed_contract, CPP, KOTLIN, CMAKE)
            changed_cpp = Path(temporary) / "wrapper.cpp"
            changed_cpp.write_text(cpp_decoy, encoding="utf-8")
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, changed_cpp, KOTLIN, CMAKE)
            changed_kotlin = Path(temporary) / "bindings.kt"
            changed_kotlin.write_text(kotlin_decoy, encoding="utf-8")
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, CPP, changed_kotlin, CMAKE)

    def test_wire_v1_cardinality_cannot_be_coordinately_reduced(self) -> None:
        contract_source = CONTRACT.read_text(encoding="utf-8")
        cpp_source = CPP.read_text(encoding="utf-8")
        kotlin_source = KOTLIN.read_text(encoding="utf-8")
        presence_block = (
            '\n[[wire.presenceFlags]]\nkotlin = "PRESENCE_PROPERTY_VALUE"\n'
            'cpp = "kHasPropertyValue"\nvalue = 16\n'
        )
        integer_block = (
            '\n[[wire.integerFields]]\nkotlin = "INTEGER_PROPERTY_FLAG"\n'
            'cpp = "kPropertyFlag"\nvalue = 7\n'
        )
        mutations = (
            (
                contract_source.replace(presence_block, "\n", 1),
                cpp_source.replace("    kHasPropertyValue = 1 << 4,\n", "", 1),
                kotlin_source.replace(
                    "    internal const val PRESENCE_PROPERTY_VALUE = 1 shl 4\n",
                    "",
                    1,
                ),
            ),
            (
                contract_source.replace("integerFieldCount = 8", "integerFieldCount = 7", 1)
                .replace(integer_block, "\n", 1),
                cpp_source.replace("constexpr int kIntegerFieldCount = 8;", "constexpr int kIntegerFieldCount = 7;", 1)
                .replace("    kPropertyFlag = 7,\n", "", 1),
                kotlin_source.replace(
                    "    internal const val INTEGER_FIELD_COUNT = 8",
                    "    internal const val INTEGER_FIELD_COUNT = 7",
                    1,
                ).replace("    internal const val INTEGER_PROPERTY_FLAG = 7\n", "", 1),
            ),
        )
        for index, (contract_mutation, cpp_mutation, kotlin_mutation) in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                changed_contract = Path(temporary) / "contract.toml"
                changed_cpp = Path(temporary) / "wrapper.cpp"
                changed_kotlin = Path(temporary) / "bindings.kt"
                changed_contract.write_text(contract_mutation, encoding="utf-8")
                changed_cpp.write_text(cpp_mutation, encoding="utf-8")
                changed_kotlin.write_text(kotlin_mutation, encoding="utf-8")
                with self.assertRaises(wrapper_contract_tool.ContractError):
                    wrapper_contract_tool.validate(
                        changed_contract,
                        changed_cpp,
                        changed_kotlin,
                        CMAKE,
                    )

    def test_cmake_output_override_and_cpp_line_splicing_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            original_cmake = CMAKE.read_text(encoding="utf-8")
            cmake_mutations = (
                original_cmake
                + "\nset_property(TARGET zivplayer_mpv PROPERTY OUTPUT_NAME renamed_mpv)\n",
                original_cmake
                + '\nset_target_properties(zivplayer_mpv PROPERTIES "OUTPUT_NAME" "renamed_mpv")\n',
                original_cmake
                + '\nset(_ziv_prop "OUTPUT_NAME")\n'
                + "set_target_properties(zivplayer_mpv PROPERTIES ${_ziv_prop} renamed_mpv)\n",
                original_cmake.replace(
                    "add_library(zivplayer_mpv SHARED zivplayer_mpv.cpp)",
                    "add_library(zivplayer_mpv SHARED another.cpp)",
                    1,
                ),
                original_cmake
                + "\nif(TRUE) target_sources(zivplayer_mpv PRIVATE evil.cpp) endif()\n",
                original_cmake
                + "\nset(_ziv_target zivplayer_mpv) "
                + "set_target_properties(${_ziv_target} PROPERTIES SOURCES evil.cpp)\n",
            )
            for index, mutation in enumerate(cmake_mutations):
                self.assertNotEqual(original_cmake, mutation)
                with self.subTest(cmake=index):
                    changed_cmake = Path(temporary) / f"CMakeLists-{index}.txt"
                    changed_cmake.write_text(mutation, encoding="utf-8")
                    with self.assertRaises(wrapper_contract_tool.ContractError):
                        wrapper_contract_tool.validate(CONTRACT, CPP, KOTLIN, changed_cmake)

            original_cpp = CPP.read_text(encoding="utf-8")
            entry_start = '        {const_cast<char*>("nativeCreate"),'
            mutation = original_cpp.replace(entry_start, "        // \\\n" + entry_start, 1)
            self.assertNotEqual(original_cpp, mutation)
            changed_cpp = Path(temporary) / "wrapper.cpp"
            changed_cpp.write_text(mutation, encoding="utf-8")
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(CONTRACT, changed_cpp, KOTLIN, CMAKE)

    def test_native_method_cardinality_cannot_be_coordinately_reduced(self) -> None:
        original_contract = CONTRACT.read_text(encoding="utf-8")
        contract_block = (
            '[[methods]]\nname = "nativeCreate"\n'
            'descriptor = "(Landroid/content/Context;)J"\n'
            'function = "GuardedNativeCreate"\n\n'
        )
        changed_contract_source = original_contract.replace("methodCount = 16", "methodCount = 15", 1)
        changed_contract_source = changed_contract_source.replace(contract_block, "", 1)
        self.assertNotEqual(original_contract, changed_contract_source)

        original_cpp = CPP.read_text(encoding="utf-8")
        cpp_entry = (
            '        {const_cast<char*>("nativeCreate"),\n'
            '         const_cast<char*>("(Landroid/content/Context;)J"),\n'
            '         reinterpret_cast<void*>(GuardedNativeCreate)},\n'
        )
        changed_cpp_source = original_cpp.replace(cpp_entry, "", 1)
        self.assertNotEqual(original_cpp, changed_cpp_source)

        original_kotlin = KOTLIN.read_text(encoding="utf-8")
        kotlin_method = "    private external fun nativeCreate(applicationContext: Context): Long\n\n"
        changed_kotlin_source = original_kotlin.replace(kotlin_method, "", 1)
        self.assertNotEqual(original_kotlin, changed_kotlin_source)

        with tempfile.TemporaryDirectory() as temporary:
            changed_contract = Path(temporary) / "contract.toml"
            changed_cpp = Path(temporary) / "wrapper.cpp"
            changed_kotlin = Path(temporary) / "bindings.kt"
            changed_contract.write_text(changed_contract_source, encoding="utf-8")
            changed_cpp.write_text(changed_cpp_source, encoding="utf-8")
            changed_kotlin.write_text(changed_kotlin_source, encoding="utf-8")
            with self.assertRaises(wrapper_contract_tool.ContractError):
                wrapper_contract_tool.validate(
                    changed_contract,
                    changed_cpp,
                    changed_kotlin,
                    CMAKE,
                )

    def test_schema_v1_names_cannot_be_coordinately_rewritten(self) -> None:
        original_contract = CONTRACT.read_text(encoding="utf-8")
        original_cpp = CPP.read_text(encoding="utf-8")
        original_kotlin = KOTLIN.read_text(encoding="utf-8")
        mutations = (
            (
                original_contract.replace("nativeCreate", "nativeConstruct"),
                original_cpp.replace("nativeCreate", "nativeConstruct"),
                original_kotlin.replace("nativeCreate", "nativeConstruct"),
            ),
            (
                original_contract.replace("INTEGER_EVENT_ID", "INTEGER_EVENT_FOO").replace(
                    "kEventId", "kEventFoo"
                ),
                original_cpp.replace("kEventId", "kEventFoo"),
                original_kotlin.replace("INTEGER_EVENT_ID", "INTEGER_EVENT_FOO"),
            ),
        )
        for index, (contract_source, cpp_source, kotlin_source) in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                changed_contract = Path(temporary) / "contract.toml"
                changed_cpp = Path(temporary) / "wrapper.cpp"
                changed_kotlin = Path(temporary) / "bindings.kt"
                changed_contract.write_text(contract_source, encoding="utf-8")
                changed_cpp.write_text(cpp_source, encoding="utf-8")
                changed_kotlin.write_text(kotlin_source, encoding="utf-8")
                with self.assertRaises(wrapper_contract_tool.ContractError):
                    wrapper_contract_tool.validate(
                        changed_contract,
                        changed_cpp,
                        changed_kotlin,
                        CMAKE,
                    )


if __name__ == "__main__":
    unittest.main()
