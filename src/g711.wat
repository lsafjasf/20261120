(module
  (memory (export "memory") 16)

  (func $mu_encode (export "mu_encode_sample") (param $sample i32) (result i32)
    (local $sign i32)
    (local $magnitude i32)
    (local $exponent i32)
    (local $mantissa i32)

    (if (i32.lt_s (local.get $sample) (i32.const 0))
      (then
        (local.set $sign (i32.const 0x80))
        (local.set $magnitude
          (i32.sub (i32.const 0) (local.get $sample))))
      (else
        (local.set $magnitude (local.get $sample))))

    (if (i32.gt_s (local.get $magnitude) (i32.const 32635))
      (then (local.set $magnitude (i32.const 32635))))

    (local.set $magnitude
      (i32.add (local.get $magnitude) (i32.const 0x84)))
    (if (i32.lt_s (local.get $magnitude) (i32.const 256))
      (then
        (local.set $exponent (i32.const 0)))
      (else
        (local.set $exponent (i32.const 1))
        (if (i32.ge_s (local.get $magnitude) (i32.const 512))
          (then (local.set $exponent (i32.const 2))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 1024))
          (then (local.set $exponent (i32.const 3))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 2048))
          (then (local.set $exponent (i32.const 4))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 4096))
          (then (local.set $exponent (i32.const 5))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 8192))
          (then (local.set $exponent (i32.const 6))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 16384))
          (then (local.set $exponent (i32.const 7))))))

    (local.set $mantissa
      (i32.and
        (i32.shr_u
          (local.get $magnitude)
          (i32.add (local.get $exponent) (i32.const 3)))
        (i32.const 0x0f)))

    (i32.and
      (i32.xor
        (i32.or
          (local.get $sign)
          (i32.or
            (i32.shl (local.get $exponent) (i32.const 4))
            (local.get $mantissa)))
        (i32.const -1))
      (i32.const 0xff)))

  (func $mu_decode (export "mu_decode_sample") (param $code i32) (result i32)
    (local $value i32)
    (local $exponent i32)
    (local $mantissa i32)
    (local $sample i32)

    (local.set $value
      (i32.and (i32.xor (local.get $code) (i32.const -1)) (i32.const 0xff)))
    (local.set $exponent
      (i32.and
        (i32.shr_u (local.get $value) (i32.const 4))
        (i32.const 7)))
    (local.set $mantissa (i32.and (local.get $value) (i32.const 0x0f)))
    (local.set $sample
      (i32.sub
        (i32.shl
          (i32.add
            (i32.shl (local.get $mantissa) (i32.const 3))
            (i32.const 0x84))
          (local.get $exponent))
        (i32.const 0x84)))

    (if (result i32) (i32.ne (i32.and (local.get $value) (i32.const 0x80)) (i32.const 0))
      (then (i32.sub (i32.const 0) (local.get $sample)))
      (else (local.get $sample))))

  (func (export "mu_loopback_sample") (param $sample i32) (result i32)
    (call $mu_decode (call $mu_encode (local.get $sample))))

  (func $a_encode (export "a_encode_sample") (param $sample i32) (result i32)
    (local $sign i32)
    (local $magnitude i32)
    (local $exponent i32)
    (local $mantissa i32)

    (if (i32.ge_s (local.get $sample) (i32.const 0))
      (then
        (local.set $sign (i32.const 0x80))
        (local.set $magnitude (local.get $sample)))
      (else
        (local.set $sign (i32.const 0))
        (local.set $magnitude
          (i32.sub (i32.const 0) (local.get $sample)))))

    (if (i32.gt_s (local.get $magnitude) (i32.const 32635))
      (then (local.set $magnitude (i32.const 32635))))

    (if (i32.lt_s (local.get $magnitude) (i32.const 256))
      (then
        (local.set $exponent (i32.const 0))
        (local.set $mantissa
          (i32.and
            (i32.shr_u (local.get $magnitude) (i32.const 4))
            (i32.const 0x0f))))
      (else
        (local.set $exponent (i32.const 1))
        (if (i32.ge_s (local.get $magnitude) (i32.const 512))
          (then (local.set $exponent (i32.const 2))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 1024))
          (then (local.set $exponent (i32.const 3))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 2048))
          (then (local.set $exponent (i32.const 4))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 4096))
          (then (local.set $exponent (i32.const 5))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 8192))
          (then (local.set $exponent (i32.const 6))))
        (if (i32.ge_s (local.get $magnitude) (i32.const 16384))
          (then (local.set $exponent (i32.const 7))))
        (local.set $mantissa
          (i32.and
            (i32.shr_u
              (local.get $magnitude)
              (i32.add (local.get $exponent) (i32.const 3)))
            (i32.const 0x0f)))))

    (i32.and
      (i32.xor
        (i32.or
          (local.get $sign)
          (i32.or
            (i32.shl (local.get $exponent) (i32.const 4))
            (local.get $mantissa)))
        (i32.const 0x55))
      (i32.const 0xff)))

  (func $a_decode (export "a_decode_sample") (param $code i32) (result i32)
    (local $value i32)
    (local $exponent i32)
    (local $mantissa i32)
    (local $sample i32)

    (local.set $value
      (i32.and (i32.xor (local.get $code) (i32.const 0x55)) (i32.const 0xff)))
    (local.set $exponent
      (i32.and
        (i32.shr_u (local.get $value) (i32.const 4))
        (i32.const 7)))
    (local.set $mantissa (i32.and (local.get $value) (i32.const 0x0f)))

    (if (i32.eqz (local.get $exponent))
      (then
        (local.set $sample
          (i32.shl
            (i32.add
              (i32.shl (local.get $mantissa) (i32.const 1))
              (i32.const 1))
            (i32.const 3))))
      (else
        (local.set $sample
          (i32.shl
            (i32.add
              (i32.add
                (i32.shl (local.get $mantissa) (i32.const 1))
                (i32.const 1))
              (i32.const 32))
            (i32.add (local.get $exponent) (i32.const 2))))))

    (if (result i32)
      (i32.ne
        (i32.and (local.get $value) (i32.const 0x80))
        (i32.const 0))
      (then (local.get $sample))
      (else (i32.sub (i32.const 0) (local.get $sample)))))

  (func (export "a_loopback_sample") (param $sample i32) (result i32)
    (call $a_decode (call $a_encode (local.get $sample))))

  (func (export "mu_encode")
    (param $input i32) (param $output i32) (param $count i32)
    (local $i i32)
    (local $offset i32)
    (block $done
      (loop $next
        (br_if $done
          (i32.ge_u (local.get $i) (local.get $count)))
        (local.set $offset
          (i32.shl (local.get $i) (i32.const 1)))
        (i32.store16
          (i32.add (local.get $output) (local.get $i))
          (call $mu_encode
            (i32.load16_s
              (i32.add (local.get $input) (local.get $offset)))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next))))

  (func (export "mu_decode")
    (param $input i32) (param $output i32) (param $count i32)
    (local $i i32)
    (local $offset i32)
    (block $done
      (loop $next
        (br_if $done
          (i32.ge_u (local.get $i) (local.get $count)))
        (local.set $offset
          (i32.shl (local.get $i) (i32.const 1)))
        (i32.store16
          (i32.add (local.get $output) (local.get $offset))
          (call $mu_decode
            (i32.load8_u
              (i32.add (local.get $input) (local.get $i)))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next))))

  (func (export "a_encode")
    (param $input i32) (param $output i32) (param $count i32)
    (local $i i32)
    (local $offset i32)
    (block $done
      (loop $next
        (br_if $done
          (i32.ge_u (local.get $i) (local.get $count)))
        (local.set $offset
          (i32.shl (local.get $i) (i32.const 1)))
        (i32.store8
          (i32.add (local.get $output) (local.get $i))
          (call $a_encode
            (i32.load16_s
              (i32.add (local.get $input) (local.get $offset)))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next))))

  (func (export "a_decode")
    (param $input i32) (param $output i32) (param $count i32)
    (local $i i32)
    (local $offset i32)
    (block $done
      (loop $next
        (br_if $done
          (i32.ge_u (local.get $i) (local.get $count)))
        (local.set $offset
          (i32.shl (local.get $i) (i32.const 1)))
        (i32.store16
          (i32.add (local.get $output) (local.get $offset))
          (call $a_decode
            (i32.load8_u
              (i32.add (local.get $input) (local.get $i)))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next))))

  (func (export "mu_loopback")
    (param $input i32) (param $output i32) (param $count i32)
    (local $i i32)
    (local $offset i32)
    (block $done
      (loop $next
        (br_if $done
          (i32.ge_u (local.get $i) (local.get $count)))
        (local.set $offset
          (i32.shl (local.get $i) (i32.const 1)))
        (i32.store16
          (i32.add (local.get $output) (local.get $offset))
          (call $mu_decode
            (call $mu_encode
              (i32.load16_s
                (i32.add (local.get $input) (local.get $offset))))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next))))

  (func (export "a_loopback")
    (param $input i32) (param $output i32) (param $count i32)
    (local $i i32)
    (local $offset i32)
    (block $done
      (loop $next
        (br_if $done
          (i32.ge_u (local.get $i) (local.get $count)))
        (local.set $offset
          (i32.shl (local.get $i) (i32.const 1)))
        (i32.store16
          (i32.add (local.get $output) (local.get $offset))
          (call $a_decode
            (call $a_encode
              (i32.load16_s
                (i32.add (local.get $input) (local.get $offset))))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next))))
)
