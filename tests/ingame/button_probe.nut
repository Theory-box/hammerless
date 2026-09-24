local b = Entities.FindByClassname(null, "func_button");
b.__KeyValueFromString("targetname", "hl_test_button");
EntFire("hl_test_button", "Press", "", 0);
printl("HLBUTTON pressed " + b);
