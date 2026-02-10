pragma circom 2.0.2;

include "../../circuits/curve.circom";
include "../../circuits/fp12.circom";
include "../../circuits/extra_curve.circom";

component main {public [P]} = Fp12MultiplyWithLineUnequal(3, 2, 2, 3, [3, 2]);
