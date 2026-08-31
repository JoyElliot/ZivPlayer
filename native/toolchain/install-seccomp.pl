#!/usr/bin/perl
# SPDX-License-Identifier: GPL-3.0-or-later

use strict;
use warnings;
use Config;

die "error: seccomp helper requires Linux x86_64 Perl\n"
    unless $^O eq "linux"
        && $Config{ptrsize} == 8
        && $Config{archname} =~ /^x86_64-linux/;
die "error: seccomp helper requires a command\n" unless @ARGV;

use constant {
    BPF_LD_W_ABS          => 0x20,
    BPF_JMP_JA            => 0x05,
    BPF_JMP_JEQ_K         => 0x15,
    BPF_JMP_JSET_K        => 0x45,
    BPF_RET_K             => 0x06,
    AUDIT_ARCH_X86_64     => 0xc000003e,
    X32_SYSCALL_BIT       => 0x40000000,
    SECCOMP_RET_KILL      => 0x80000000,
    SECCOMP_RET_ERRNO     => 0x00050000,
    SECCOMP_RET_ALLOW     => 0x7fff0000,
    PR_SET_NO_NEW_PRIVS   => 38,
    PR_SET_SECCOMP        => 22,
    SECCOMP_MODE_FILTER   => 2,
    SYS_PRCTL             => 157,
    AF_UNIX               => 1,
    EPERM_VALUE           => 1,
    ENOSYS_VALUE          => 38,
    NAMESPACE_CLONE_FLAGS => 0x7e020080,
};

my @instructions;
my %labels;

sub mark_label {
    my ($name) = @_;
    die "error: duplicate seccomp label $name\n" if exists $labels{$name};
    $labels{$name} = scalar @instructions;
}

sub statement {
    my ($code, $value) = @_;
    push @instructions, [$code, 0, 0, $value];
}

sub conditional_jump {
    my ($code, $value, $true_label, $false_label) = @_;
    push @instructions, [$code, $true_label, $false_label, $value];
}

sub unconditional_jump {
    my ($target) = @_;
    push @instructions, [BPF_JMP_JA, 0, 0, $target];
}

# struct seccomp_data: nr@0, arch@4, args[0]@16 on x86_64.
statement(BPF_LD_W_ABS, 4);
conditional_jump(BPF_JMP_JEQ_K, AUDIT_ARCH_X86_64, "load_nr", "kill");
mark_label("load_nr");
statement(BPF_LD_W_ABS, 0);
conditional_jump(BPF_JMP_JSET_K, X32_SYSCALL_BIT, "kill", "dispatch_socket");

mark_label("dispatch_socket");
conditional_jump(BPF_JMP_JEQ_K, 41, "check_socket_family", "dispatch_socketpair");
mark_label("dispatch_socketpair");
conditional_jump(BPF_JMP_JEQ_K, 53, "check_socket_family", "dispatch_clone");
mark_label("dispatch_clone");
conditional_jump(BPF_JMP_JEQ_K, 56, "check_clone_flags", "dispatch_clone3");
mark_label("dispatch_clone3");
conditional_jump(BPF_JMP_JEQ_K, 435, "deny_enosys", "dispatch_unshare");
mark_label("dispatch_unshare");
conditional_jump(BPF_JMP_JEQ_K, 272, "deny_eperm", "dispatch_setns");
mark_label("dispatch_setns");
conditional_jump(BPF_JMP_JEQ_K, 308, "deny_eperm", "dispatch_add_key");
mark_label("dispatch_add_key");
conditional_jump(BPF_JMP_JEQ_K, 248, "deny_eperm", "dispatch_request_key");
mark_label("dispatch_request_key");
conditional_jump(BPF_JMP_JEQ_K, 249, "deny_eperm", "dispatch_keyctl");
mark_label("dispatch_keyctl");
conditional_jump(BPF_JMP_JEQ_K, 250, "deny_eperm", "dispatch_perf");
mark_label("dispatch_perf");
conditional_jump(BPF_JMP_JEQ_K, 298, "deny_eperm", "dispatch_bpf");
mark_label("dispatch_bpf");
conditional_jump(BPF_JMP_JEQ_K, 321, "deny_eperm", "dispatch_userfaultfd");
mark_label("dispatch_userfaultfd");
conditional_jump(BPF_JMP_JEQ_K, 323, "deny_eperm", "dispatch_io_setup");
mark_label("dispatch_io_setup");
conditional_jump(BPF_JMP_JEQ_K, 425, "deny_eperm", "dispatch_io_enter");
mark_label("dispatch_io_enter");
conditional_jump(BPF_JMP_JEQ_K, 426, "deny_eperm", "dispatch_io_register");
mark_label("dispatch_io_register");
conditional_jump(BPF_JMP_JEQ_K, 427, "deny_eperm", "allow");

mark_label("check_socket_family");
statement(BPF_LD_W_ABS, 16);
conditional_jump(BPF_JMP_JEQ_K, AF_UNIX, "allow", "deny_eperm");
mark_label("check_clone_flags");
statement(BPF_LD_W_ABS, 16);
conditional_jump(BPF_JMP_JSET_K, NAMESPACE_CLONE_FLAGS, "deny_eperm", "allow");

mark_label("deny_eperm");
statement(BPF_RET_K, SECCOMP_RET_ERRNO | EPERM_VALUE);
mark_label("deny_enosys");
statement(BPF_RET_K, SECCOMP_RET_ERRNO | ENOSYS_VALUE);
mark_label("kill");
statement(BPF_RET_K, SECCOMP_RET_KILL);
mark_label("allow");
statement(BPF_RET_K, SECCOMP_RET_ALLOW);

my $program = "";
for my $index (0 .. $#instructions) {
    my ($code, $true_target, $false_target, $value) = @{$instructions[$index]};
    my ($jump_true, $jump_false) = (0, 0);
    if ($code == BPF_JMP_JA) {
        die "error: undefined seccomp target $value\n" unless exists $labels{$value};
        my $distance = $labels{$value} - $index - 1;
        die "error: invalid seccomp jump distance\n" if $distance < 0;
        $value = $distance;
    } elsif ($code == BPF_JMP_JEQ_K || $code == BPF_JMP_JSET_K) {
        die "error: undefined seccomp target\n"
            unless exists $labels{$true_target} && exists $labels{$false_target};
        $jump_true = $labels{$true_target} - $index - 1;
        $jump_false = $labels{$false_target} - $index - 1;
        die "error: invalid seccomp conditional jump distance\n"
            if $jump_true < 0 || $jump_true > 255 || $jump_false < 0 || $jump_false > 255;
    }
    $program .= pack("vCCV", $code, $jump_true, $jump_false, $value);
}

my $filter_pointer = unpack("J", pack("p", $program));
my $filter_program = pack("S x6 J", scalar(@instructions), $filter_pointer);
my $program_pointer = unpack("J", pack("p", $filter_program));

syscall(SYS_PRCTL, PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0
    or die "error: cannot set no_new_privs before seccomp: $!\n";
syscall(SYS_PRCTL, PR_SET_SECCOMP, SECCOMP_MODE_FILTER, $program_pointer, 0, 0) == 0
    or die "error: cannot install seccomp filter: $!\n";

open(my $status, "<", "/proc/self/status")
    or die "error: cannot read seccomp status: $!\n";
my ($mode, $filters);
while (my $line = <$status>) {
    $mode = $1 if $line =~ /^Seccomp:\s+(\d+)\s*$/;
    $filters = $1 if $line =~ /^Seccomp_filters:\s+(\d+)\s*$/;
}
close($status) or die "error: cannot close seccomp status: $!\n";
die "error: seccomp filter did not become active\n"
    unless defined($mode) && $mode == 2 && defined($filters) && $filters >= 1;

exec {$ARGV[0]} @ARGV;
die "error: cannot exec seccomp-confined command: $!\n";
