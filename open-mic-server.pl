#!/usr/bin/env perl
use strict;
use warnings;
use JSON::PP;
use HTTP::Server::PSGI;

my $buffer_file = '/tmp/opencode_mic_buffer.txt';
my $last_sent = '';
my $paused = 0;
my $focus_mode = 1;
my $auto_exec = 0;
my $auto_exec_pid = 0;
my @word_counts = ();

my $cdp_bridge = '/home/rj/su/cdp_bridge.py';
$SIG{CHLD} = 'IGNORE';

sub send_keys {
    my (@keys) = @_;
    return unless $focus_mode;
    my $key_str = join(',', @keys);
    if ($key_str eq 'Escape') {
        system("python3 $cdp_bridge escape 3 >/dev/null 2>&1");
    } elsif ($key_str eq 'Enter') {
        system("python3 $cdp_bridge enter >/dev/null 2>&1");
    } elsif ($key_str eq 'Tab') {
        system("python3 $cdp_bridge agent_next >/dev/null 2>&1");
    } elsif ($key_str eq 'C-w') {
        system("python3 $cdp_bridge delete_word >/dev/null 2>&1");
    } elsif ($key_str eq 'C-u') {
        system("python3 $cdp_bridge clear_line >/dev/null 2>&1");
    }
}

sub send_text {
    my ($text) = @_;
    if ($focus_mode) {
        my $escaped = $text;
        $escaped =~ s/'/'\\''/g;
        system("python3 $cdp_bridge text '$escaped' >/dev/null 2>&1");
    } else {
        system('xdotool', 'type', $text);
    }
}

sub spawn_auto_exec_timer {
    return unless $auto_exec;
    if ($auto_exec_pid && kill 0, $auto_exec_pid) {
        kill 'TERM', $auto_exec_pid;
    }
    $auto_exec_pid = fork;
    if ($auto_exec_pid == 0) {
        close STDIN; close STDOUT; close STDERR;
        sleep 2;
        send_keys('Enter');
        exit 0;
    }
}

sub cancel_auto_exec_timer {
    if ($auto_exec_pid && kill 0, $auto_exec_pid) {
        kill 'TERM', $auto_exec_pid;
    }
    $auto_exec_pid = 0;
}

sub app {
    my $env = shift;

    if ($env->{REQUEST_METHOD} eq 'POST' && $env->{PATH_INFO} eq '/') {
        my $content;
        my $input = $env->{'psgi.input'};
        read($input, $content, $env->{CONTENT_LENGTH} || 0);

        if ($content) {
            my $data = JSON::PP::decode_json($content);
            my $text = $data->{text} // '';
            process_text($text) if length $text;
        }

        return [200, ['Content-Type' => 'application/json'], [JSON::PP::encode_json({ok => JSON::PP::true})]];
    }

    return [404, ['Content-Type' => 'application/json'], [JSON::PP::encode_json({error => 'not found'})]];
}

sub process_text {
    my ($chunk) = @_;

    $chunk =~ s/\[.+?\]//g;
    $chunk =~ s/^\s+|\s+$//g;
    return if $chunk eq '';

    my $buffer = '';
    if (-f $buffer_file) {
        open my $fh, '<', $buffer_file or warn "read_buffer: $!";
        if ($fh) {
            $buffer = <$fh>;
            close $fh;
        }
    }

    my $combined = $buffer . " " . $chunk;

    if ($combined =~ /\bstop\W*stop\b/i) {
        print "KEYWORD: stop stop -> Escape x3\n";
        for (1 .. 3) {
            send_keys('Escape');
        }
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\bgo\W*go\b/i) {
        print "KEYWORD: go go -> Enter\n";
        send_keys('Enter');
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\b(?:enter|execute)\b/i) {
        print "KEYWORD: enter/execute -> Enter\n";
        send_keys('Enter');
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\b(?:undo|revert)\b/i) {
        my $n = () = $combined =~ /\b(?:undo|revert)\b/gi;
        print "KEYWORD: undo/revert x $n\n";
        for (1 .. $n) {
            my $wc = pop @word_counts or last;
            send_keys(('C-w') x $wc);
        }
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\bdelete\W*word\b/i) {
        print "KEYWORD: delete word -> C-w\n";
        send_keys('C-w');
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\b(?:clear\W*line|erase\W*text)\b/i) {
        print "KEYWORD: clear line/erase text -> C-u\n";
        send_keys('C-u');
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\btab\b|\bnext\W*agent\b/i) {
        print "KEYWORD: tab/next agent -> Tab\n";
        send_keys('Tab');
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\bpause\W*work\b/i) {
        $paused = 1;
        print "KEYWORD: pause work -> paused=$paused\n";
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\bresume\W*work\b/i) {
        $paused = 0;
        print "KEYWORD: resume work -> paused=$paused\n";
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\bfocus\W*off\b/i) {
        $focus_mode = 0;
        print "KEYWORD: focus off -> focus_mode=$focus_mode\n";
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\bfocus\W*on\b/i) {
        $focus_mode = 1;
        print "KEYWORD: focus on -> focus_mode=$focus_mode\n";
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\b(?:focus|activate)\W*terminal\b/i) {
        print "KEYWORD: focus/activate terminal -> GUI\n";
        my $wid = `xdotool search --name "^OpenCode$"`;
        chomp $wid;
        system('wmctrl', '-i', '-a', $wid) if $wid;
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\benable\W*automatic\W*execution\b/i) {
        $auto_exec = 1;
        print "KEYWORD: enable automatic execution\n";
        clear_buffer();
        $last_sent = '';
        return;
    }

    if ($combined =~ /\bdisable\W*automatic\W*execution\b/i) {
        $auto_exec = 0;
        cancel_auto_exec_timer();
        print "KEYWORD: disable automatic execution\n";
        clear_buffer();
        $last_sent = '';
        return;
    }

    # filter known hallucinations
    if ($chunk =~ /^i['´`]?m\s+not\s+sure\.?\s*$/i) {
        print "FILTER (hallucination): $chunk\n";
        return;
    }
    if ($chunk =~ /^i['´`]?m\s+not\s+going\s+to\s+get\s+it\.?\s*$/i) {
        print "FILTER (hallucination): $chunk\n";
        return;
    }
    if ($chunk =~ /click/i) {
        print "FILTER (click): $chunk\n";
        return;
    }

    # if paused, discard non-keyword chunks
    if ($paused) {
        print "PAUSED (dropped): $chunk\n";
        return;
    }

    # dedup: skip if same as last chunk
    if ($chunk eq $last_sent) {
        print "SKIP (duplicate): $chunk\n";
        return;
    }

    # replace punctuation words with symbols
    $chunk =~ s/\bperiod\b/./gi;
    $chunk =~ s/\bcomma\b/,/gi;
    $chunk =~ s/\bquestion\s*mark\b/?/gi;
    $chunk =~ s/\bexclamation\s*mark\b/!/gi;
    $chunk =~ s/\bdash\b/-/gi;
    $chunk =~ s/\bslash\b/\//gi;
    $chunk =~ s/\bcolon\b/:/gi;
    $chunk =~ s/\bsemicolon\b/;/gi;
    $chunk =~ s/ ([.,!?:;])/$1/g;

    my $to_send = " $chunk";
    print "SEND:$to_send";
    send_text($to_send);
    print " [OK]\n";
    $last_sent = $chunk;

    my $wc = scalar(split(/\s+/, $chunk));
    push @word_counts, $wc if $wc > 0;

    my $tail = length($chunk) > 10 ? substr($chunk, -10) : $chunk;
    save_buffer($tail);
    spawn_auto_exec_timer();
}

sub clear_buffer {
    unlink $buffer_file;
}

sub save_buffer {
    my ($content) = @_;
    open my $fh, '>', $buffer_file or warn "save_buffer: $!";
    if ($fh) {
        print $fh $content;
        close $fh;
    }
}

unless (caller) {
    my $port = shift(@ARGV) || 9876;
    my $server = HTTP::Server::PSGI->new(
        host => '0.0.0.0',
        port => $port,
    );
    print "OpenCodeMic server listening on http://0.0.0.0:$port\n";
    print "Buffer: $buffer_file\n";
    $server->run(\&app);
}

1;
__END__
=head1 NAME

open-mic-server.pl - PC web service for OpenCodeMic

=head1 SYNOPSIS

    perl open-mic-server.pl [port]

    # or with plackup:
    plackup open-mic-server.pl

=head1 DESCRIPTION

Listens for POST requests from the OpenCodeMic Android app.
Sends keystrokes to the active window and the opencode GUI via CDP.

Modes:
    "focus on"   - Send to opencode GUI via CDP bridge (default)
    "focus off"  - xdotool type to the active window only (no CDP, no key combos)
    "activate terminal" - Bring the opencode GUI window to the foreground

Keywords:    "stop stop"  - Send Escape x3 (cancel/clear)
    "go go"      - Send Enter (submit)
    everything else - Type the text
