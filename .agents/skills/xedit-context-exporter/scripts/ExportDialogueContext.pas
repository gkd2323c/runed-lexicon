{
  Read-only dialogue context export for skyrim-mod-translator.
  Exports deterministic DIAL -> INFO structure plus raw evidence used to
  resolve speakers without semantic guessing.
}
unit ExportDialogueContext;

const
  OutputFileName = 'skyrim_mod_translator_dialogue_context.json';
  TargetFileNameConfig = 'skyrim_mod_translator_target_plugin.txt';

function HexFormID(e: IInterface): string;
begin
  Result := IntToHex(GetLoadOrderFormID(e), 8);
end;

function HexLocalFormID(e: IInterface): string;
begin
  Result := IntToHex(FixedFormID(e) and $00FFFFFF, 6);
end;

function FindConfiguredTargetFile: IInterface;
var
  Config: TStringList;
  TargetName: string;
  i: Integer;
begin
  Result := nil;
  Config := TStringList.Create;
  try
    Config.LoadFromFile(DataPath + TargetFileNameConfig);
    if Config.Count < 1 then begin
      AddMessage('SMT_ERROR: target plugin config is empty');
      Exit;
    end;
    TargetName := Trim(Config[0]);
  finally
    Config.Free;
  end;

  for i := 0 to FileCount - 1 do
    if GetFileName(FileByIndex(i)) = TargetName then begin
      Result := FileByIndex(i);
      Exit;
    end;

  AddMessage('SMT_ERROR: configured target plugin is not loaded: ' + TargetName);
end;

procedure FillRecordIdentity(Obj: TJsonObject; Rec: IInterface);
begin
  if not Assigned(Rec) then
    Exit;
  Obj.S['signature'] := Signature(Rec);
  Obj.S['form_id'] := HexFormID(Rec);
  Obj.S['local_form_id'] := HexLocalFormID(Rec);
  Obj.S['editor_id'] := EditorID(Rec);
  Obj.S['full_name'] := GetElementEditValues(Rec, 'FULL');
end;

procedure AddLinkedRecord(Obj: TJsonObject; const Key: string; Elem: IInterface);
var
  Rec: IInterface;
  Child: TJsonObject;
begin
  if not Assigned(Elem) then
    Exit;
  Rec := LinksTo(Elem);
  if not Assigned(Rec) then
    Exit;

  Child := Obj.O[Key];
  FillRecordIdentity(Child, Rec);
end;

procedure FillNpcEvidence(Obj: TJsonObject; Npc: IInterface);
var
  Factions, Entry, Faction: IInterface;
  Arr: TJsonArray;
  Item: TJsonObject;
  i: Integer;
begin
  FillRecordIdentity(Obj, Npc);
  if not Assigned(Npc) or (Signature(Npc) <> 'NPC_') then
    Exit;

  AddLinkedRecord(Obj, 'template', ElementByName(Npc, 'TPLT - Template'));
  AddLinkedRecord(Obj, 'race', ElementByName(Npc, 'RNAM - Race'));
  AddLinkedRecord(Obj, 'direct_voice_type', ElementByName(Npc, 'VTCK - Voice'));

  Arr := Obj.A['direct_factions'];
  Factions := ElementByName(Npc, 'Factions');
  if not Assigned(Factions) then
    Exit;
  for i := 0 to ElementCount(Factions) - 1 do begin
    Entry := ElementByIndex(Factions, i);
    Faction := LinksTo(ElementByName(Entry, 'Faction'));
    if not Assigned(Faction) then
      Continue;
    Item := Arr.AddObject;
    FillRecordIdentity(Item, Faction);
    Item.I['rank'] := GetElementNativeValues(Entry, 'Rank');
  end;
end;

procedure CollectTemplateDescendants(BaseNpc: IInterface; Arr: TJsonArray;
  Seen: TStringList; Depth: Integer);
var
  Ref, TemplateElem, TemplateNpc: IInterface;
  i: Integer;
  Key: string;
begin
  if not Assigned(BaseNpc) or (Depth > 12) then
    Exit;

  for i := 0 to ReferencedByCount(BaseNpc) - 1 do begin
    Ref := ReferencedByIndex(BaseNpc, i);
    if Signature(Ref) <> 'NPC_' then
      Continue;
    Ref := WinningOverride(Ref);
    TemplateElem := ElementByName(Ref, 'TPLT - Template');
    if not Assigned(TemplateElem) then
      Continue;
    TemplateNpc := LinksTo(TemplateElem);
    if not Assigned(TemplateNpc) then
      Continue;
    if GetLoadOrderFormID(TemplateNpc) <> GetLoadOrderFormID(BaseNpc) then
      Continue;

    Key := HexFormID(Ref);
    if Seen.IndexOf(Key) <> -1 then
      Continue;
    Seen.Add(Key);
    FillNpcEvidence(Arr.AddObject, Ref);
    CollectTemplateDescendants(Ref, Arr, Seen, Depth + 1);
  end;
end;

procedure CollectInheritedVoiceDescendants(BaseNpc: IInterface; Users: TStringList;
  Depth: Integer);
var
  Ref, TemplateElem, TemplateNpc: IInterface;
  i: Integer;
  Key: string;
begin
  if not Assigned(BaseNpc) or (Depth > 16) then
    Exit;

  for i := 0 to ReferencedByCount(BaseNpc) - 1 do begin
    Ref := ReferencedByIndex(BaseNpc, i);
    if Signature(Ref) <> 'NPC_' then
      Continue;
    Ref := WinningOverride(Ref);

    TemplateElem := ElementByName(Ref, 'TPLT - Template');
    if not Assigned(TemplateElem) then
      Continue;
    TemplateNpc := LinksTo(TemplateElem);
    if not Assigned(TemplateNpc) then
      Continue;
    if GetLoadOrderFormID(TemplateNpc) <> GetLoadOrderFormID(BaseNpc) then
      Continue;

    // Match the voice-resolution rule used by xEdit's bundled
    // Export dialogues.pas: direct VTCK wins; otherwise follow TPLT.
    if ElementExists(Ref, 'VTCK - Voice') then
      Continue;

    Key := HexFormID(Ref);
    if Users.IndexOf(Key) <> -1 then
      Continue;
    Users.AddObject(Key, Ref);
    CollectInheritedVoiceDescendants(Ref, Users, Depth + 1);
  end;
end;

function TemplateRoot(Npc: IInterface): IInterface;
var
  Seen: TStringList;
  TemplateElem, TemplateNpc: IInterface;
  Key: string;
begin
  Result := nil;
  if not Assigned(Npc) then
    Exit;

  Seen := TStringList.Create;
  try
    Npc := WinningOverride(Npc);
    while Assigned(Npc) and (Signature(Npc) = 'NPC_') do begin
      Key := HexFormID(Npc);
      if Seen.IndexOf(Key) <> -1 then
        Break;
      Seen.Add(Key);
      Result := Npc;

      TemplateElem := ElementByName(Npc, 'TPLT - Template');
      if not Assigned(TemplateElem) then
        Break;
      TemplateNpc := LinksTo(TemplateElem);
      if not Assigned(TemplateNpc) or (Signature(TemplateNpc) <> 'NPC_') then
        Break;
      Npc := WinningOverride(TemplateNpc);
    end;
  finally
    Seen.Free;
  end;
end;

function ResolveActorRecord(Rec: IInterface): IInterface;
var
  Base: IInterface;
begin
  Result := nil;
  if not Assigned(Rec) then
    Exit;

  Rec := WinningOverride(Rec);
  if Signature(Rec) = 'NPC_' then begin
    Result := Rec;
    Exit;
  end;

  if (Signature(Rec) = 'ACHR') or (Signature(Rec) = 'REFR') then begin
    Base := BaseRecord(Rec);
    if Assigned(Base) and (Signature(Base) = 'NPC_') then
      Result := WinningOverride(Base);
  end;
end;

function FindAlias(Quest: IInterface; AliasNumber: Integer): IInterface;
var
  Aliases, Alias: IInterface;
  i: Integer;
begin
  Result := nil;
  if not Assigned(Quest) then
    Exit;

  Quest := WinningOverride(Quest);
  Aliases := ElementByName(Quest, 'Aliases');
  if not Assigned(Aliases) then
    Exit;

  for i := 0 to ElementCount(Aliases) - 1 do begin
    Alias := ElementByIndex(Aliases, i);
    if GetNativeValue(ElementByIndex(Alias, 0)) = AliasNumber then begin
      Result := Alias;
      Exit;
    end;
  end;
end;

function ResolveAliasActor(Quest: IInterface; AliasNumber, Depth: Integer): IInterface;
var
  Alias, Rec, ExternalQuest: IInterface;
  ExternalAlias: Integer;
begin
  Result := nil;
  if Depth > 8 then
    Exit;

  Alias := FindAlias(Quest, AliasNumber);
  if not Assigned(Alias) then
    Exit;

  if ElementExists(Alias, 'ALUA - Unique Actor') then begin
    Rec := LinksTo(ElementByName(Alias, 'ALUA - Unique Actor'));
    Result := ResolveActorRecord(Rec);
    Exit;
  end;

  if ElementExists(Alias, 'ALFR - Forced Reference') then begin
    Rec := LinksTo(ElementByName(Alias, 'ALFR - Forced Reference'));
    Result := ResolveActorRecord(Rec);
    Exit;
  end;

  if ElementExists(Alias, 'External Alias Reference') then begin
    ExternalQuest := LinksTo(ElementByPath(Alias, 'External Alias Reference\ALEQ - Quest'));
    ExternalAlias := GetElementNativeValues(Alias, 'External Alias Reference\ALEA - Alias');
    Result := ResolveAliasActor(ExternalQuest, ExternalAlias, Depth + 1);
  end;
end;

procedure AddSpeakerCandidate(Candidates: TJsonArray; Seen: TStringList;
  const Method, Evidence: string; Rec: IInterface);
var
  Actor: IInterface;
  Key: string;
  Obj: TJsonObject;
begin
  Actor := ResolveActorRecord(Rec);
  if not Assigned(Actor) then
    Exit;

  Key := Method + '|' + HexFormID(Actor);
  if Seen.IndexOf(Key) <> -1 then
    Exit;
  Seen.Add(Key);

  Obj := Candidates.AddObject;
  Obj.S['method'] := Method;
  Obj.S['evidence'] := Evidence;
  FillRecordIdentity(Obj.O['actor'], Actor);
end;

procedure AddResponses(Obj: TJsonObject; Info: IInterface);
var
  Responses, Response: IInterface;
  Arr: TJsonArray;
  Item: TJsonObject;
  i: Integer;
begin
  Arr := Obj.A['responses'];
  Responses := ElementByName(Info, 'Responses');
  if not Assigned(Responses) then
    Exit;

  for i := 0 to ElementCount(Responses) - 1 do begin
    Response := ElementByIndex(Responses, i);
    Item := Arr.AddObject;
    Item.I['number'] := GetElementNativeValues(Response, 'TRDT\Response number');
    Item.S['text'] := GetElementEditValues(Response, 'NAM1 - Response Text');
    Item.S['emotion_type'] := GetElementEditValues(Response, 'TRDT\Emotion Type');
    Item.I['emotion_value'] := GetElementNativeValues(Response, 'TRDT\Emotion Value');
    Item.S['script_notes'] := GetElementEditValues(Response, 'NAM2 - Script Notes');
  end;
end;

procedure AddDirectVoiceTypeUsers(ConditionObj: TJsonObject; VoiceType: IInterface;
  Candidates: TJsonArray; CandidateSeen: TStringList);
var
  Users, InheritedUsers, EffectiveUsers, RaceUsers: TJsonArray;
  Seen, InheritedSeen, EffectiveSeen, RaceSeen: TStringList;
  Ref, VoiceElem, LinkedVoice, Actor, Root, OtherRoot: IInterface;
  i: Integer;
  Key, RootID, ActorName, OtherName: string;
  SameCharacterLineage: Boolean;
begin
  Users := ConditionObj.A['direct_npc_users'];
  InheritedUsers := ConditionObj.A['template_inherited_npc_users'];
  EffectiveUsers := ConditionObj.A['effective_npc_users'];
  RaceUsers := ConditionObj.A['race_users'];
  Seen := TStringList.Create;
  InheritedSeen := TStringList.Create;
  EffectiveSeen := TStringList.Create;
  RaceSeen := TStringList.Create;
  try
    for i := 0 to ReferencedByCount(VoiceType) - 1 do begin
      Ref := ReferencedByIndex(VoiceType, i);
      if Signature(Ref) = 'NPC_' then begin
        Ref := WinningOverride(Ref);
        VoiceElem := ElementByName(Ref, 'VTCK - Voice');
        if not Assigned(VoiceElem) then
          Continue;
        LinkedVoice := LinksTo(VoiceElem);
        if not Assigned(LinkedVoice) then
          Continue;
        if GetLoadOrderFormID(LinkedVoice) <> GetLoadOrderFormID(VoiceType) then
          Continue;

        Key := HexFormID(Ref);
        if Seen.IndexOf(Key) <> -1 then
          Continue;
        Seen.Add(Key);
        FillNpcEvidence(Users.AddObject, Ref);
        CollectTemplateDescendants(Ref, InheritedUsers, InheritedSeen, 0);

        if EffectiveSeen.IndexOf(Key) = -1 then begin
          EffectiveSeen.AddObject(Key, Ref);
          FillNpcEvidence(EffectiveUsers.AddObject, Ref);
        end;
        CollectInheritedVoiceDescendants(Ref, EffectiveSeen, 0);
      end
      else if Signature(Ref) = 'RACE' then begin
        Ref := WinningOverride(Ref);
        Key := HexFormID(Ref);
        if RaceSeen.IndexOf(Key) <> -1 then
          Continue;
        RaceSeen.Add(Key);
        FillRecordIdentity(RaceUsers.AddObject, Ref);
      end;
    end;

    // CollectInheritedVoiceDescendants stores inherited NPC objects in
    // EffectiveSeen, so append any entries that were not direct users.
    for i := 0 to EffectiveSeen.Count - 1 do begin
      Key := EffectiveSeen[i];
      if Seen.IndexOf(Key) <> -1 then
        Continue;
      Actor := ObjectToElement(EffectiveSeen.Objects[i]);
      FillNpcEvidence(EffectiveUsers.AddObject, Actor);
    end;

    if EffectiveSeen.Count = 1 then begin
      Actor := ObjectToElement(EffectiveSeen.Objects[0]);
      AddSpeakerCandidate(Candidates, CandidateSeen,
        'condition:GetIsVoiceType:unique_effective_npc',
        'GetIsVoiceType resolves to one effective NPC after direct/template voice resolution',
        Actor);
    end
    else if EffectiveSeen.Count > 1 then begin
      Actor := ObjectToElement(EffectiveSeen.Objects[0]);
      Root := TemplateRoot(Actor);
      if Assigned(Root) then begin
        RootID := HexFormID(Root);
        ActorName := GetElementEditValues(Actor, 'FULL');
        SameCharacterLineage := (ActorName <> '') and (Pos('<Error:', ActorName) = 0);

        for i := 1 to EffectiveSeen.Count - 1 do begin
          Actor := ObjectToElement(EffectiveSeen.Objects[i]);
          OtherRoot := TemplateRoot(Actor);
          OtherName := GetElementEditValues(Actor, 'FULL');
          if (not Assigned(OtherRoot)) or
             (HexFormID(OtherRoot) <> RootID) or
             (OtherName <> ActorName)
          then begin
            SameCharacterLineage := False;
            Break;
          end;
        end;

        if SameCharacterLineage then
          AddSpeakerCandidate(Candidates, CandidateSeen,
            'condition:GetIsVoiceType:single_character_template_lineage',
            'all effective NPC records share one template root and display name', Root);
      end;
    end;
  finally
    Seen.Free;
    InheritedSeen.Free;
    EffectiveSeen.Free;
    RaceSeen.Free;
  end;
end;

procedure AddDirectFactionMembers(ConditionObj: TJsonObject; Faction: IInterface);
var
  Members, InheritedMembers: TJsonArray;
  Seen, InheritedSeen: TStringList;
  Ref: IInterface;
  i: Integer;
  Key: string;
begin
  Members := ConditionObj.A['direct_npc_members'];
  InheritedMembers := ConditionObj.A['template_inherited_npc_members'];
  Seen := TStringList.Create;
  InheritedSeen := TStringList.Create;
  try
    for i := 0 to ReferencedByCount(Faction) - 1 do begin
      Ref := ReferencedByIndex(Faction, i);
      if Signature(Ref) <> 'NPC_' then
        Continue;
      Ref := WinningOverride(Ref);
      Key := HexFormID(Ref);
      if Seen.IndexOf(Key) <> -1 then
        Continue;
      Seen.Add(Key);
      FillNpcEvidence(Members.AddObject, Ref);
      CollectTemplateDescendants(Ref, InheritedMembers, InheritedSeen, 0);
    end;
  finally
    Seen.Free;
    InheritedSeen.Free;
  end;
end;

function NpcHasDirectFaction(Npc, Faction: IInterface): Boolean;
var
  Factions, Entry, LinkedFaction: IInterface;
  i: Integer;
begin
  Result := False;
  if not Assigned(Npc) or not Assigned(Faction) then
    Exit;

  Factions := ElementByName(Npc, 'Factions');
  if not Assigned(Factions) then
    Exit;

  for i := 0 to ElementCount(Factions) - 1 do begin
    Entry := ElementByIndex(Factions, i);
    LinkedFaction := LinksTo(ElementByName(Entry, 'Faction'));
    if not Assigned(LinkedFaction) then
      Continue;
    if GetLoadOrderFormID(LinkedFaction) = GetLoadOrderFormID(Faction) then begin
      Result := True;
      Exit;
    end;
  end;
end;

procedure AddScannedFactionMembers(ConditionObj: TJsonObject; Faction: IInterface);
var
  Members: TJsonArray;
  Seen: TStringList;
  FileObj, GroupObj, Npc: IInterface;
  f, i: Integer;
  Key: string;
begin
  Members := ConditionObj.A['scanned_direct_npc_members'];
  Seen := TStringList.Create;
  try
    for f := 0 to FileCount - 1 do begin
      FileObj := FileByIndex(f);
      GroupObj := GroupBySignature(FileObj, 'NPC_');
      if not Assigned(GroupObj) then
        Continue;

      for i := 0 to ElementCount(GroupObj) - 1 do begin
        Npc := ElementByIndex(GroupObj, i);
        if Signature(Npc) <> 'NPC_' then
          Continue;
        Npc := WinningOverride(Npc);
        if GetIsDeleted(Npc) then
          Continue;

        Key := HexFormID(Npc);
        if Seen.IndexOf(Key) <> -1 then
          Continue;
        Seen.Add(Key);

        if NpcHasDirectFaction(Npc, Faction) then
          FillNpcEvidence(Members.AddObject, Npc);
      end;
    end;
  finally
    Seen.Free;
  end;
end;

procedure AddActorsFromReferenceGroup(Actors: TJsonArray; Seen: TStringList;
  Group: IInterface);
var
  Ref, Actor: IInterface;
  i: Integer;
  Key: string;
begin
  if not Assigned(Group) then
    Exit;

  for i := 0 to ElementCount(Group) - 1 do begin
    Ref := ElementByIndex(Group, i);
    if Signature(Ref) <> 'ACHR' then
      Continue;
    Actor := ResolveActorRecord(Ref);
    if not Assigned(Actor) then
      Continue;
    Key := HexFormID(Actor);
    if Seen.IndexOf(Key) <> -1 then
      Continue;
    Seen.Add(Key);
    FillNpcEvidence(Actors.AddObject, Actor);
  end;
end;

procedure AddCellPlacedActorsFromVersion(Actors: TJsonArray; Seen: TStringList;
  CellVersion: IInterface);
var
  CellChildren, PersistentGroup, TemporaryGroup: IInterface;
begin
  if not Assigned(CellVersion) then
    Exit;
  CellChildren := ChildGroup(CellVersion);
  if not Assigned(CellChildren) then
    Exit;

  PersistentGroup := FindChildGroup(CellChildren, 8, CellVersion);
  TemporaryGroup := FindChildGroup(CellChildren, 9, CellVersion);
  AddActorsFromReferenceGroup(Actors, Seen, PersistentGroup);
  AddActorsFromReferenceGroup(Actors, Seen, TemporaryGroup);
end;

procedure AddCellPlacedActors(ConditionObj: TJsonObject; Cell: IInterface);
var
  Actors: TJsonArray;
  Seen: TStringList;
  BaseCell, CellVersion: IInterface;
  i: Integer;
begin
  Actors := ConditionObj.A['placed_actor_bases'];
  Seen := TStringList.Create;
  try
    BaseCell := MasterOrSelf(Cell);
    if not Assigned(BaseCell) then
      BaseCell := Cell;

    // Child references are distributed across the master CELL and its
    // overrides.  Looking only at WinningOverride(Cell) can omit most of the
    // original placed actors, so merge every CELL version by actor-base ID.
    AddCellPlacedActorsFromVersion(Actors, Seen, BaseCell);
    for i := 0 to OverrideCount(BaseCell) - 1 do begin
      CellVersion := OverrideByIndex(BaseCell, i);
      AddCellPlacedActorsFromVersion(Actors, Seen, CellVersion);
    end;
  finally
    Seen.Free;
  end;
end;

procedure AddConditionLinkedParameter(ConditionObj: TJsonObject;
  const Key: string; Condition: IInterface; const Path: string);
var
  Elem, Rec: IInterface;
begin
  Elem := ElementByPath(Condition, Path);
  if not Assigned(Elem) then
    Exit;
  Rec := LinksTo(Elem);
  if not Assigned(Rec) then
    Exit;
  FillRecordIdentity(ConditionObj.O[Key], Rec);
end;

procedure AddConditions(Obj: TJsonObject; Info, Quest: IInterface;
  Candidates: TJsonArray; Seen: TStringList);
var
  Conditions, Condition, Elem, Rec, Actor: IInterface;
  Arr: TJsonArray;
  Item: TJsonObject;
  i, AliasNumber: Integer;
  Func: string;
begin
  Arr := Obj.A['conditions'];
  Conditions := ElementByName(Info, 'Conditions');
  if not Assigned(Conditions) then
    Exit;

  for i := 0 to ElementCount(Conditions) - 1 do begin
    Condition := ElementByIndex(Conditions, i);
    Func := GetElementEditValues(Condition, 'CTDA\Function');

    Item := Arr.AddObject;
    Item.I['index'] := i;
    Item.S['function'] := Func;
    Item.S['display'] := Name(Condition);
    Item.S['edit_value'] := GetEditValue(Condition);
    Item.S['type'] := GetElementEditValues(Condition, 'CTDA\Type');
    Item.I['type_native'] := GetElementNativeValues(Condition, 'CTDA\Type');
    Item.S['run_on'] := GetElementEditValues(Condition, 'CTDA\Run On');
    Item.S['comparison_value'] := GetElementEditValues(Condition, 'CTDA\Comparison Value - Float');
    AddConditionLinkedParameter(Item, 'run_on_reference', Condition, 'CTDA\Reference');

    if Func = 'GetIsID' then begin
      Elem := ElementByPath(Condition, 'CTDA\Referenceable Object');
      if Assigned(Elem) then begin
        Rec := LinksTo(Elem);
        if Assigned(Rec) then begin
          FillRecordIdentity(Item.O['reference'], Rec);
          AddSpeakerCandidate(Candidates, Seen, 'condition:GetIsID',
            'INFO condition ' + IntToStr(i), Rec);
        end;
      end;
    end
    else if Func = 'GetIsAliasRef' then begin
      AliasNumber := GetElementNativeValues(Condition, 'CTDA\Alias');
      Item.I['alias_id'] := AliasNumber;
      Actor := ResolveAliasActor(Quest, AliasNumber, 0);
      if Assigned(Actor) then begin
        FillRecordIdentity(Item.O['resolved_alias_actor'], Actor);
        AddSpeakerCandidate(Candidates, Seen, 'condition:GetIsAliasRef',
          'INFO condition ' + IntToStr(i) + ', quest alias ' + IntToStr(AliasNumber), Actor);
      end;
    end
    else if Func = 'GetIsVoiceType' then begin
      Elem := ElementByPath(Condition, 'CTDA\Voice Type');
      if Assigned(Elem) then begin
        Rec := LinksTo(Elem);
        if Assigned(Rec) then begin
          FillRecordIdentity(Item.O['voice_type'], Rec);
          AddDirectVoiceTypeUsers(Item, Rec, Candidates, Seen);
        end;
      end;
    end
    else if Func = 'GetInCell' then begin
      AddConditionLinkedParameter(Item, 'cell', Condition, 'CTDA\Cell');
      Elem := ElementByPath(Condition, 'CTDA\Cell');
      if Assigned(Elem) then begin
        Rec := LinksTo(Elem);
        if Assigned(Rec) then
          AddCellPlacedActors(Item, Rec);
      end;
    end
    else if (Func = 'GetInFaction') or (Func = 'GetFactionRank') then begin
      AddConditionLinkedParameter(Item, 'faction', Condition, 'CTDA\Faction');
      Elem := ElementByPath(Condition, 'CTDA\Faction');
      if Assigned(Elem) then begin
        Rec := LinksTo(Elem);
        if Assigned(Rec) then begin
          AddDirectFactionMembers(Item, Rec);
          AddScannedFactionMembers(Item, Rec);
        end;
      end;
    end
    else if (Func = 'GetIsRace') or (Func = 'GetPCIsRace') then begin
      AddConditionLinkedParameter(Item, 'race', Condition, 'CTDA\Race');
    end
    else if Func = 'GetGlobalValue' then begin
      AddConditionLinkedParameter(Item, 'global', Condition, 'CTDA\Global');
    end
    else if (Func = 'GetStage') or (Func = 'GetStageDone') then begin
      AddConditionLinkedParameter(Item, 'quest', Condition, 'CTDA\Quest');
    end
    else if Func = 'GetItemCount' then begin
      AddConditionLinkedParameter(Item, 'inventory_object', Condition, 'CTDA\Inventory Object');
    end
    else if Func = 'GetDeadCount' then begin
      AddConditionLinkedParameter(Item, 'actor_base', Condition, 'CTDA\Actor Base');
    end
    else if Func = 'GetVMQuestVariable' then begin
      AddConditionLinkedParameter(Item, 'quest', Condition, 'CTDA\Quest');
      Item.S['variable_name'] := GetElementEditValues(Condition, 'CTDA\Variable Name');
    end;
  end;
end;

procedure AddInfo(Infos: TJsonArray; Info, Quest: IInterface);
var
  Obj: TJsonObject;
  ExplicitElem, ExplicitRec: IInterface;
  Candidates: TJsonArray;
  Seen: TStringList;
begin
  Obj := Infos.AddObject;
  Obj.S['form_id'] := HexFormID(Info);
  Obj.S['local_form_id'] := HexLocalFormID(Info);
  Obj.S['editor_id'] := EditorID(Info);
  Obj.S['prompt'] := GetElementEditValues(Info, 'RNAM - Prompt');

  AddLinkedRecord(Obj, 'previous_info', ElementByName(Info, 'PNAM - Previous INFO'));

  Candidates := Obj.A['speaker_candidates'];
  Seen := TStringList.Create;
  try
    ExplicitElem := ElementByName(Info, 'ANAM - Speaker');
    if Assigned(ExplicitElem) then begin
      ExplicitRec := LinksTo(ExplicitElem);
      if Assigned(ExplicitRec) then begin
        FillRecordIdentity(Obj.O['explicit_speaker'], ExplicitRec);
        AddSpeakerCandidate(Candidates, Seen, 'explicit:ANAM', 'ANAM - Speaker', ExplicitRec);
      end;
    end;

    AddConditions(Obj, Info, Quest, Candidates, Seen);
    AddResponses(Obj, Info);
  finally
    Seen.Free;
  end;
end;

procedure AddAliasRawFields(AliasObj: TJsonObject; Alias: IInterface);
var
  Fields: TJsonArray;
  FieldObj: TJsonObject;
  Elem: IInterface;
  i: Integer;
begin
  Fields := AliasObj.A['fields'];
  for i := 0 to ElementCount(Alias) - 1 do begin
    Elem := ElementByIndex(Alias, i);
    FieldObj := Fields.AddObject;
    FieldObj.S['name'] := Name(Elem);
    FieldObj.S['path'] := Path(Elem);
    FieldObj.S['edit_value'] := GetEditValue(Elem);
  end;
end;

procedure AddQuestAliases(QuestObj: TJsonObject; Quest: IInterface);
var
  Aliases, Alias, ExternalQuest: IInterface;
  Arr: TJsonArray;
  AliasObj: TJsonObject;
  i: Integer;
begin
  Arr := QuestObj.A['aliases'];
  Aliases := ElementByName(Quest, 'Aliases');
  if not Assigned(Aliases) then
    Exit;

  for i := 0 to ElementCount(Aliases) - 1 do begin
    Alias := ElementByIndex(Aliases, i);
    AliasObj := Arr.AddObject;
    AliasObj.I['alias_id'] := GetNativeValue(ElementByIndex(Alias, 0));
    AliasObj.S['name'] := Name(Alias);
    AliasObj.S['path'] := Path(Alias);

    AddLinkedRecord(AliasObj, 'forced_reference', ElementByName(Alias, 'ALFR - Forced Reference'));
    AddLinkedRecord(AliasObj, 'unique_actor', ElementByName(Alias, 'ALUA - Unique Actor'));
    AddLinkedRecord(AliasObj, 'voice_types', ElementByName(Alias, 'VTCK - Voice Types'));

    if ElementExists(Alias, 'External Alias Reference') then begin
      ExternalQuest := LinksTo(ElementByPath(Alias, 'External Alias Reference\ALEQ - Quest'));
      if Assigned(ExternalQuest) then
        FillRecordIdentity(AliasObj.O['external_alias_quest'], ExternalQuest);
      AliasObj.I['external_alias_id'] := GetElementNativeValues(
        Alias, 'External Alias Reference\ALEA - Alias');
    end;

    AddAliasRawFields(AliasObj, Alias);
  end;
end;

procedure AddQuest(Quests: TJsonArray; Quest: IInterface);
var
  Obj: TJsonObject;
begin
  Obj := Quests.AddObject;
  FillRecordIdentity(Obj, Quest);
  AddQuestAliases(Obj, Quest);
end;

procedure AddTargetQuests(Root: TJsonObject; TargetFile: IInterface);
var
  Quests: TJsonArray;
  QuestGroup, Quest: IInterface;
  i: Integer;
begin
  Quests := Root.A['quests'];
  QuestGroup := GroupBySignature(TargetFile, 'QUST');
  if not Assigned(QuestGroup) then
    Exit;

  for i := 0 to ElementCount(QuestGroup) - 1 do begin
    Quest := ElementByIndex(QuestGroup, i);
    if Signature(Quest) = 'QUST' then
      AddQuest(Quests, Quest);
  end;
end;

procedure AddDialogue(Dialogues: TJsonArray; Dial: IInterface);
var
  Obj: TJsonObject;
  Infos: TJsonArray;
  InfoGroup, Info, Quest: IInterface;
  i: Integer;
begin
  Obj := Dialogues.AddObject;
  Obj.S['form_id'] := HexFormID(Dial);
  Obj.S['local_form_id'] := HexLocalFormID(Dial);
  Obj.S['editor_id'] := EditorID(Dial);
  Obj.S['topic_text'] := GetElementEditValues(Dial, 'FULL');
  Obj.S['category'] := GetElementEditValues(Dial, 'DATA\Category');
  Obj.S['type'] := GetElementEditValues(Dial, 'SNAM');
  Obj.S['subtype'] := GetElementEditValues(Dial, 'DATA\Subtype');
  AddLinkedRecord(Obj, 'quest', ElementByName(Dial, 'QNAM - Quest'));
  AddLinkedRecord(Obj, 'branch', ElementByName(Dial, 'BNAM - Branch'));

  Quest := nil;
  if ElementExists(Dial, 'QNAM - Quest') then
    Quest := LinksTo(ElementByName(Dial, 'QNAM - Quest'));

  Infos := Obj.A['infos'];
  InfoGroup := ChildGroup(Dial);
  if not Assigned(InfoGroup) then
    Exit;

  for i := 0 to ElementCount(InfoGroup) - 1 do begin
    Info := ElementByIndex(InfoGroup, i);
    if Signature(Info) = 'INFO' then
      AddInfo(Infos, Info, Quest);
  end;
end;

function Initialize: Integer;
var
  Root, PluginObj: TJsonObject;
  Dialogues: TJsonArray;
  TargetFile, DialGroup, Dial: IInterface;
  i: Integer;
  OutputPath: string;
begin
  Result := 1;

  if FileCount < 1 then begin
    AddMessage('SMT_ERROR: no plugins loaded');
    Exit;
  end;

  TargetFile := FindConfiguredTargetFile;
  if not Assigned(TargetFile) then
    Exit;
  Root := TJsonObject.Create;
  try
    Root.I['schema_version'] := 1;
    PluginObj := Root.O['plugin'];
    PluginObj.S['filename'] := GetFileName(TargetFile);
    PluginObj.I['file_index'] := FileCount - 1;
    Dialogues := Root.A['dialogues'];
    AddTargetQuests(Root, TargetFile);

    DialGroup := GroupBySignature(TargetFile, 'DIAL');
    if Assigned(DialGroup) then
      for i := 0 to ElementCount(DialGroup) - 1 do begin
        Dial := ElementByIndex(DialGroup, i);
        if Signature(Dial) = 'DIAL' then begin
          AddDialogue(Dialogues, Dial);
          if (i mod 25) = 0 then
            AddMessage('SMT_PROGRESS: processed ' + IntToStr(i) + ' DIAL records');
        end;
      end;

    Root.I['dialogue_count'] := Dialogues.Count;

    OutputPath := DataPath + OutputFileName;
    try
      Root.SaveToFile(OutputPath, False, TEncoding.UTF8, True);
    except
      on E: Exception do begin
        AddMessage('SMT_ERROR: SaveToFile failed for ' + OutputPath + ': ' + E.Message);
        Exit;
      end;
    end;
    if not FileExists(OutputPath) then begin
      AddMessage('SMT_ERROR: output file not found after save: ' + OutputPath);
      Exit;
    end;
    AddMessage('SMT_EXPORT_OK: ' + OutputPath);
  finally
    Root.Free;
  end;
end;

end.
