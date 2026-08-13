//! Windows kernel Job supervisor for Knowe's MXC executor.
//!
//! The process is deliberately dependency-free.  It creates `wxc-exec.exe`
//! suspended, assigns it to a non-breakaway Job, then resumes it.  Closing this
//! launcher (including a crash/TerminateProcess) closes the last Job handle and
//! the kernel kills wxc plus every descendant.  The launcher's original backend
//! parent is also monitored so a backend crash cannot orphan the sandbox.

#![cfg_attr(not(windows), allow(dead_code))]

#[cfg(not(windows))]
fn main() {
    eprintln!("knowe-sandbox-launcher is Windows-only");
    std::process::exit(125);
}

#[cfg(windows)]
mod windows_launcher {
    use std::ffi::{c_void, OsStr, OsString};
    use std::io::{self, Write};
    use std::mem::{size_of, zeroed};
    use std::os::windows::ffi::OsStrExt;
    use std::ptr::{null, null_mut};

    type Bool = i32;
    type Dword = u32;
    type Handle = *mut c_void;
    type SizeT = usize;

    const FALSE: Bool = 0;
    const TRUE: Bool = 1;
    const INFINITE: Dword = 0xffff_ffff;
    const WAIT_OBJECT_0: Dword = 0;
    const WAIT_TIMEOUT: Dword = 0x0000_0102;
    const WAIT_FAILED: Dword = 0xffff_ffff;
    const SYNCHRONIZE: Dword = 0x0010_0000;
    const TOKEN_QUERY: Dword = 0x0000_0008;
    const TOKEN_INTEGRITY_LEVEL: Dword = 25;
    const TOKEN_IS_APP_CONTAINER: Dword = 29;
    const TOKEN_APP_CONTAINER_SID: Dword = 31;
    const CREATE_SUSPENDED: Dword = 0x0000_0004;
    const CREATE_NEW_PROCESS_GROUP: Dword = 0x0000_0200;
    const CREATE_NO_WINDOW: Dword = 0x0800_0000;
    const STARTF_USESTDHANDLES: Dword = 0x0000_0100;
    const STD_INPUT_HANDLE: Dword = -10i32 as u32;
    const STD_OUTPUT_HANDLE: Dword = -11i32 as u32;
    const STD_ERROR_HANDLE: Dword = -12i32 as u32;
    const HANDLE_FLAG_INHERIT: Dword = 0x0000_0001;

    const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS: Dword = 9;
    const JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION_CLASS: Dword = 15;
    const JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS: Dword = 1;
    const JOB_OBJECT_LIMIT_ACTIVE_PROCESS: Dword = 0x0000_0008;
    const JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION: Dword = 0x0000_0400;
    const JOB_OBJECT_LIMIT_JOB_MEMORY: Dword = 0x0000_0200;
    const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Dword = 0x0000_2000;
    const JOB_OBJECT_CPU_RATE_CONTROL_ENABLE: Dword = 0x1;
    const JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP: Dword = 0x4;

    // Aggregate limits include wxc-exec and every sandbox descendant.  No
    // BREAKAWAY_OK/SILENT_BREAKAWAY_OK flag is ever enabled.
    const ACTIVE_PROCESS_LIMIT: Dword = 128;
    const JOB_MEMORY_LIMIT_BYTES: SizeT = 4 * 1024 * 1024 * 1024;
    const CPU_RATE_HARD_CAP: Dword = 7_500; // 75.00% of machine CPU cycles.
    const SUPERVISOR_FAILURE_EXIT: Dword = 125;
    const SUPERVISOR_TIMEOUT_EXIT: Dword = 124;
    const JOB_POLL_INTERVAL_MS: Dword = 10;

    #[repr(C)]
    struct StartupInfoW {
        cb: Dword,
        reserved: *mut u16,
        desktop: *mut u16,
        title: *mut u16,
        x: Dword,
        y: Dword,
        x_size: Dword,
        y_size: Dword,
        x_count_chars: Dword,
        y_count_chars: Dword,
        fill_attribute: Dword,
        flags: Dword,
        show_window: u16,
        reserved2_size: u16,
        reserved2: *mut u8,
        stdin: Handle,
        stdout: Handle,
        stderr: Handle,
    }

    #[repr(C)]
    struct ProcessInformation {
        process: Handle,
        thread: Handle,
        process_id: Dword,
        thread_id: Dword,
    }

    #[repr(C)]
    #[derive(Default)]
    struct IoCounters {
        read_operation_count: u64,
        write_operation_count: u64,
        other_operation_count: u64,
        read_transfer_count: u64,
        write_transfer_count: u64,
        other_transfer_count: u64,
    }

    #[repr(C)]
    #[derive(Default)]
    struct BasicLimitInformation {
        per_process_user_time_limit: i64,
        per_job_user_time_limit: i64,
        limit_flags: Dword,
        minimum_working_set_size: SizeT,
        maximum_working_set_size: SizeT,
        active_process_limit: Dword,
        affinity: SizeT,
        priority_class: Dword,
        scheduling_class: Dword,
    }

    #[repr(C)]
    #[derive(Default)]
    struct ExtendedLimitInformation {
        basic: BasicLimitInformation,
        io: IoCounters,
        process_memory_limit: SizeT,
        job_memory_limit: SizeT,
        peak_process_memory_used: SizeT,
        peak_job_memory_used: SizeT,
    }

    #[repr(C)]
    struct CpuRateControlInformation {
        control_flags: Dword,
        cpu_rate: Dword,
    }

    #[repr(C)]
    #[derive(Default)]
    struct BasicAccountingInformation {
        total_user_time: i64,
        total_kernel_time: i64,
        this_period_total_user_time: i64,
        this_period_total_kernel_time: i64,
        total_page_fault_count: Dword,
        total_processes: Dword,
        active_processes: Dword,
        total_terminated_processes: Dword,
    }

    #[repr(C)]
    struct SidAndAttributes {
        sid: *mut c_void,
        attributes: Dword,
    }

    #[repr(C)]
    struct TokenMandatoryLabel {
        label: SidAndAttributes,
    }

    #[repr(C)]
    struct TokenAppContainerInformation {
        token_app_container: *mut c_void,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(attributes: *const c_void, name: *const u16) -> Handle;
        fn SetInformationJobObject(
            job: Handle,
            info_class: Dword,
            info: *const c_void,
            length: Dword,
        ) -> Bool;
        fn QueryInformationJobObject(
            job: Handle,
            info_class: Dword,
            info: *mut c_void,
            length: Dword,
            return_length: *mut Dword,
        ) -> Bool;
        fn AssignProcessToJobObject(job: Handle, process: Handle) -> Bool;
        fn TerminateJobObject(job: Handle, exit_code: Dword) -> Bool;
        fn CreateProcessW(
            application_name: *const u16,
            command_line: *mut u16,
            process_attributes: *const c_void,
            thread_attributes: *const c_void,
            inherit_handles: Bool,
            creation_flags: Dword,
            environment: *const c_void,
            current_directory: *const u16,
            startup_info: *const StartupInfoW,
            process_information: *mut ProcessInformation,
        ) -> Bool;
        fn ResumeThread(thread: Handle) -> Dword;
        fn WaitForMultipleObjects(
            count: Dword,
            handles: *const Handle,
            wait_all: Bool,
            milliseconds: Dword,
        ) -> Dword;
        fn WaitForSingleObject(handle: Handle, milliseconds: Dword) -> Dword;
        fn GetExitCodeProcess(process: Handle, exit_code: *mut Dword) -> Bool;
        fn GetStdHandle(which: Dword) -> Handle;
        fn SetHandleInformation(handle: Handle, mask: Dword, flags: Dword) -> Bool;
        fn OpenProcess(access: Dword, inherit: Bool, process_id: Dword) -> Handle;
        fn CloseHandle(handle: Handle) -> Bool;
        fn GetLastError() -> Dword;
        fn GetCurrentProcess() -> Handle;
        fn LocalFree(memory: Handle) -> Handle;
        fn GetTickCount64() -> u64;
        fn ExitProcess(exit_code: Dword) -> !;
    }

    #[link(name = "advapi32")]
    extern "system" {
        fn OpenProcessToken(process: Handle, access: Dword, token: *mut Handle) -> Bool;
        fn GetTokenInformation(
            token: Handle,
            information_class: Dword,
            information: *mut c_void,
            information_length: Dword,
            return_length: *mut Dword,
        ) -> Bool;
        fn ConvertSidToStringSidW(sid: *const c_void, string_sid: *mut *mut u16) -> Bool;
        fn GetSidSubAuthorityCount(sid: *const c_void) -> *mut u8;
        fn GetSidSubAuthority(sid: *const c_void, index: Dword) -> *mut Dword;
    }

    struct OwnedHandle(Handle);

    impl OwnedHandle {
        fn new(handle: Handle, operation: &str) -> io::Result<Self> {
            if handle.is_null() || handle as isize == -1 {
                Err(last_error(operation))
            } else {
                Ok(Self(handle))
            }
        }
    }

    impl Drop for OwnedHandle {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }

    fn last_error(operation: &str) -> io::Error {
        let code = unsafe { GetLastError() } as i32;
        io::Error::other(format!("{operation} failed: WinError {code}"))
    }

    fn wide_null(value: &OsStr) -> Vec<u16> {
        value.encode_wide().chain(Some(0)).collect()
    }

    fn quote_windows_arg(value: &OsStr) -> OsString {
        let text = value.to_string_lossy();
        if !text.is_empty()
            && !text
                .chars()
                .any(|character| character == ' ' || character == '\t' || character == '"')
        {
            return OsString::from(text.as_ref());
        }
        let mut quoted = String::from("\"");
        let mut backslashes = 0usize;
        for character in text.chars() {
            if character == '\\' {
                backslashes += 1;
                continue;
            }
            if character == '"' {
                quoted.push_str(&"\\".repeat(backslashes * 2 + 1));
                quoted.push('"');
            } else {
                quoted.push_str(&"\\".repeat(backslashes));
                quoted.push(character);
            }
            backslashes = 0;
        }
        quoted.push_str(&"\\".repeat(backslashes * 2));
        quoted.push('"');
        OsString::from(quoted)
    }

    fn command_line(arguments: &[OsString]) -> Vec<u16> {
        let mut result = OsString::new();
        for (index, argument) in arguments.iter().enumerate() {
            if index > 0 {
                result.push(" ");
            }
            result.push(quote_windows_arg(argument));
        }
        wide_null(&result)
    }

    fn parse_arguments() -> Result<(Dword, Dword, Vec<OsString>), String> {
        let arguments: Vec<OsString> = std::env::args_os().skip(1).collect();
        if arguments.len() < 4 || arguments[0] != "--parent-pid" {
            return Err(
                "usage: knowe-sandbox-launcher --parent-pid <pid> [--timeout-ms <ms>] -- <wxc-exec> [args...]"
                    .to_string(),
            );
        }
        let parent_pid = arguments[1]
            .to_string_lossy()
            .parse::<Dword>()
            .map_err(|_| "invalid --parent-pid".to_string())?;
        if parent_pid == 0 {
            return Err("invalid launcher arguments".to_string());
        }
        let mut separator = 2usize;
        let mut timeout_ms = 0;
        if arguments
            .get(separator)
            .is_some_and(|value| value == "--timeout-ms")
        {
            timeout_ms = arguments
                .get(separator + 1)
                .ok_or_else(|| "missing --timeout-ms value".to_string())?
                .to_string_lossy()
                .parse::<Dword>()
                .map_err(|_| "invalid --timeout-ms".to_string())?;
            separator += 2;
        }
        if arguments.get(separator).is_none_or(|value| value != "--")
            || arguments
                .get(separator + 1)
                .is_none_or(|value| value.as_os_str().is_empty())
        {
            return Err("invalid launcher arguments".to_string());
        }
        Ok((parent_pid, timeout_ms, arguments[separator + 1..].to_vec()))
    }

    fn test_mode() -> Option<Result<Dword, String>> {
        let arguments: Vec<OsString> = std::env::args_os().skip(1).collect();
        let mode = arguments.first()?.to_string_lossy();
        if mode == "--test-token-info" {
            if arguments.len() != 1 {
                return Some(Err("invalid token-info test arguments".to_string()));
            }
            return Some(print_token_info());
        }
        if mode != "--test-delayed-write" && mode != "--test-spawn-delayed-child" {
            return None;
        }
        if arguments.len() != 3 {
            return Some(Err(
                "invalid sandbox launcher test-mode arguments".to_string()
            ));
        }
        let delay_ms = match arguments[1].to_string_lossy().parse::<u64>() {
            Ok(value) => value,
            Err(_) => return Some(Err("invalid sandbox launcher test delay".to_string())),
        };
        let marker = arguments[2].clone();
        if mode == "--test-delayed-write" {
            std::thread::sleep(std::time::Duration::from_millis(delay_ms));
            return Some(
                std::fs::write(&marker, b"escaped")
                    .map(|_| 0)
                    .map_err(|error| format!("test delayed write failed: {error}")),
            );
        }

        let executable = match std::env::current_exe() {
            Ok(value) => value,
            Err(error) => return Some(Err(format!("test current_exe failed: {error}"))),
        };
        match std::process::Command::new(executable)
            .arg("--test-delayed-write")
            .arg(delay_ms.to_string())
            .arg(marker)
            .spawn()
        {
            Ok(_child) => {
                println!("descendant-started");
                let _ = std::io::stdout().flush();
                std::thread::sleep(std::time::Duration::from_secs(30));
                Some(Ok(0))
            }
            Err(error) => Some(Err(format!("test descendant spawn failed: {error}"))),
        }
    }

    fn token_information(token: Handle, class: Dword) -> Result<Vec<u8>, String> {
        let mut needed = 0;
        unsafe {
            GetTokenInformation(token, class, null_mut(), 0, &mut needed);
        }
        if needed == 0 {
            return Err(last_error("GetTokenInformation(size)").to_string());
        }
        let mut buffer = vec![0u8; needed as usize];
        if unsafe {
            GetTokenInformation(
                token,
                class,
                buffer.as_mut_ptr() as *mut c_void,
                needed,
                &mut needed,
            )
        } == FALSE
        {
            return Err(last_error("GetTokenInformation(data)").to_string());
        }
        Ok(buffer)
    }

    fn sid_string(sid: *const c_void) -> Result<String, String> {
        if sid.is_null() {
            return Ok(String::new());
        }
        let mut value: *mut u16 = null_mut();
        if unsafe { ConvertSidToStringSidW(sid, &mut value) } == FALSE || value.is_null() {
            return Err(last_error("ConvertSidToStringSidW").to_string());
        }
        let mut length = 0usize;
        unsafe {
            while *value.add(length) != 0 {
                length += 1;
            }
        }
        let result = String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(value, length) });
        unsafe {
            LocalFree(value as Handle);
        }
        Ok(result)
    }

    fn print_token_info() -> Result<Dword, String> {
        let mut token_handle: Handle = null_mut();
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token_handle) } == FALSE
        {
            return Err(last_error("OpenProcessToken").to_string());
        }
        let token = OwnedHandle::new(token_handle, "OpenProcessToken")
            .map_err(|error| error.to_string())?;

        let app_flag = token_information(token.0, TOKEN_IS_APP_CONTAINER)?;
        if app_flag.len() < size_of::<Dword>() {
            return Err("TokenIsAppContainer result is truncated".to_string());
        }
        let is_app_container =
            unsafe { std::ptr::read_unaligned(app_flag.as_ptr() as *const Dword) } != 0;

        let app_info = token_information(token.0, TOKEN_APP_CONTAINER_SID)?;
        if app_info.len() < size_of::<TokenAppContainerInformation>() {
            return Err("TokenAppContainerSid result is truncated".to_string());
        }
        let app_sid = unsafe {
            std::ptr::read_unaligned(app_info.as_ptr() as *const TokenAppContainerInformation)
                .token_app_container
        };
        let app_sid_text = sid_string(app_sid)?;

        let integrity_info = token_information(token.0, TOKEN_INTEGRITY_LEVEL)?;
        if integrity_info.len() < size_of::<TokenMandatoryLabel>() {
            return Err("TokenIntegrityLevel result is truncated".to_string());
        }
        let integrity_sid = unsafe {
            std::ptr::read_unaligned(integrity_info.as_ptr() as *const TokenMandatoryLabel)
                .label
                .sid
        };
        let integrity_sid_text = sid_string(integrity_sid)?;
        let sub_authority_count = unsafe { GetSidSubAuthorityCount(integrity_sid) };
        if sub_authority_count.is_null() || unsafe { *sub_authority_count } == 0 {
            return Err("integrity SID has no sub-authority".to_string());
        }
        let rid =
            unsafe { *GetSidSubAuthority(integrity_sid, Dword::from(*sub_authority_count) - 1) };
        println!(
            "{{\"is_app_container\":{},\"app_container_sid\":\"{}\",\"integrity_sid\":\"{}\",\"integrity_rid\":{}}}",
            is_app_container, app_sid_text, integrity_sid_text, rid
        );
        let _ = std::io::stdout().flush();
        Ok(0)
    }

    fn configure_job(job: Handle) -> io::Result<()> {
        let mut limits = ExtendedLimitInformation::default();
        limits.basic.limit_flags = JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            | JOB_OBJECT_LIMIT_JOB_MEMORY
            | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        limits.basic.active_process_limit = ACTIVE_PROCESS_LIMIT;
        limits.job_memory_limit = JOB_MEMORY_LIMIT_BYTES;
        if unsafe {
            SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                &limits as *const _ as *const c_void,
                size_of::<ExtendedLimitInformation>() as Dword,
            )
        } == FALSE
        {
            return Err(last_error("SetInformationJobObject(extended limits)"));
        }

        let cpu = CpuRateControlInformation {
            control_flags: JOB_OBJECT_CPU_RATE_CONTROL_ENABLE
                | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
            cpu_rate: CPU_RATE_HARD_CAP,
        };
        if unsafe {
            SetInformationJobObject(
                job,
                JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION_CLASS,
                &cpu as *const _ as *const c_void,
                size_of::<CpuRateControlInformation>() as Dword,
            )
        } == FALSE
        {
            return Err(last_error("SetInformationJobObject(CPU hard cap)"));
        }
        Ok(())
    }

    fn active_processes(job: Handle) -> io::Result<Dword> {
        let mut accounting = BasicAccountingInformation::default();
        if unsafe {
            QueryInformationJobObject(
                job,
                JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
                &mut accounting as *mut _ as *mut c_void,
                size_of::<BasicAccountingInformation>() as Dword,
                null_mut(),
            )
        } == FALSE
        {
            return Err(last_error("QueryInformationJobObject(accounting)"));
        }
        Ok(accounting.active_processes)
    }

    fn make_inheritable(handle: Handle) -> io::Result<()> {
        if handle.is_null() || handle as isize == -1 {
            return Ok(());
        }
        if unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) }
            == FALSE
        {
            return Err(last_error("SetHandleInformation(stdio)"));
        }
        Ok(())
    }

    fn execute() -> Result<Dword, String> {
        let (parent_pid, timeout_ms, arguments) = parse_arguments()?;
        let parent = OwnedHandle::new(
            unsafe { OpenProcess(SYNCHRONIZE, FALSE, parent_pid) },
            "OpenProcess(parent)",
        )
        .map_err(|error| error.to_string())?;
        let job = OwnedHandle::new(
            unsafe { CreateJobObjectW(null(), null()) },
            "CreateJobObjectW",
        )
        .map_err(|error| error.to_string())?;
        configure_job(job.0).map_err(|error| error.to_string())?;

        let stdin = unsafe { GetStdHandle(STD_INPUT_HANDLE) };
        let stdout = unsafe { GetStdHandle(STD_OUTPUT_HANDLE) };
        let stderr = unsafe { GetStdHandle(STD_ERROR_HANDLE) };
        for handle in [stdin, stdout, stderr] {
            make_inheritable(handle).map_err(|error| error.to_string())?;
        }
        let mut startup: StartupInfoW = unsafe { zeroed() };
        startup.cb = size_of::<StartupInfoW>() as Dword;
        startup.flags = STARTF_USESTDHANDLES;
        startup.stdin = stdin;
        startup.stdout = stdout;
        startup.stderr = stderr;
        let mut process: ProcessInformation = unsafe { zeroed() };
        let application = wide_null(&arguments[0]);
        let mut command = command_line(&arguments);
        if unsafe {
            CreateProcessW(
                application.as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                TRUE,
                CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                null(),
                null(),
                &startup,
                &mut process,
            )
        } == FALSE
        {
            return Err(last_error("CreateProcessW(wxc-exec)").to_string());
        }
        let child_process = OwnedHandle(process.process);
        let child_thread = OwnedHandle(process.thread);
        if unsafe { AssignProcessToJobObject(job.0, child_process.0) } == FALSE {
            unsafe {
                TerminateJobObject(job.0, SUPERVISOR_FAILURE_EXIT);
            }
            return Err(last_error("AssignProcessToJobObject").to_string());
        }
        if unsafe { ResumeThread(child_thread.0) } == Dword::MAX {
            unsafe {
                TerminateJobObject(job.0, SUPERVISOR_FAILURE_EXIT);
            }
            return Err(last_error("ResumeThread").to_string());
        }

        let handles = [child_process.0, parent.0];
        let mut workload_deadline: Option<u64> = None;
        loop {
            let waited = unsafe {
                WaitForMultipleObjects(
                    handles.len() as Dword,
                    handles.as_ptr(),
                    FALSE,
                    JOB_POLL_INTERVAL_MS,
                )
            };
            if waited == WAIT_OBJECT_0 {
                break;
            }
            if waited == WAIT_OBJECT_0 + 1 {
                unsafe {
                    TerminateJobObject(job.0, SUPERVISOR_FAILURE_EXIT);
                    WaitForSingleObject(child_process.0, INFINITE);
                }
                return Ok(SUPERVISOR_FAILURE_EXIT);
            }
            if waited == WAIT_FAILED || waited != WAIT_TIMEOUT {
                unsafe {
                    TerminateJobObject(job.0, SUPERVISOR_FAILURE_EXIT);
                }
                return Err(last_error("WaitForMultipleObjects").to_string());
            }

            // wxc-exec is the first Job member.  Start the user timeout only
            // when it creates the contained workload, so Tier-3 DACL setup is
            // not charged to model code.  MXC 0.7's own timeout terminates its
            // inner Job but can leave a descendant alive while wxc restores
            // DACLs; this outer non-breakaway Job is the authoritative kill.
            if timeout_ms > 0
                && workload_deadline.is_none()
                && active_processes(job.0).map_err(|e| e.to_string())? > 1
            {
                workload_deadline =
                    Some(unsafe { GetTickCount64() }.saturating_add(u64::from(timeout_ms)));
            }
            if workload_deadline.is_some_and(|deadline| unsafe { GetTickCount64() } >= deadline) {
                if unsafe { TerminateJobObject(job.0, SUPERVISOR_TIMEOUT_EXIT) } == FALSE {
                    return Err(last_error("TerminateJobObject(timeout)").to_string());
                }
                unsafe {
                    WaitForSingleObject(child_process.0, INFINITE);
                }
                return Ok(SUPERVISOR_TIMEOUT_EXIT);
            }
        }
        let mut exit_code = SUPERVISOR_FAILURE_EXIT;
        if unsafe { GetExitCodeProcess(child_process.0, &mut exit_code) } == FALSE {
            return Err(last_error("GetExitCodeProcess").to_string());
        }
        Ok(exit_code)
    }

    pub fn main() {
        let result = test_mode().unwrap_or_else(execute);
        match result {
            Ok(code) => unsafe { ExitProcess(code) },
            Err(message) => {
                eprintln!("knowe-sandbox-launcher: {message}");
                unsafe { ExitProcess(SUPERVISOR_FAILURE_EXIT) }
            }
        }
    }
}

#[cfg(windows)]
fn main() {
    windows_launcher::main();
}
